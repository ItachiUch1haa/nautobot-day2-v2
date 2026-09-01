"""
Catalogs the shadow IP the moment a real IP is created (via onboarding
device sync, onboarding_mcp's deploy step, or any future IP creation) OR an
existing real IP's address value is changed (a statically-configured device
being manually re-IP'd in Nautobot) -- this does not create anything on the
network, only records the mapping the FortiGate NVA's NAT already covers.
Both triggers converge on the same recompute-and-relink logic below, since
the shadow IP is always derived live from whatever real IP is currently
recorded, never from a remembered pairing (VIP Management architecture doc
§4, §6.1).

Job Hook configuration (Extensibility -> Job Hooks, a manual one-time
Nautobot-admin step, not something code can register): trigger on
IPAddress, actions "create" **and** "update", scoped to any namespace
except Global (to avoid re-triggering on the shadow record itself). If this
hook was previously configured for "create" only, it needs updating in the
Nautobot UI to add "update" for the re-IP path below to actually fire.
"""
from nautobot.apps.jobs import JobHookReceiver, register_jobs
from nautobot.dcim.models import Device
from nautobot.extras.models import Status
from nautobot.ipam.models import IPAddress, Namespace, Prefix

from ..shadow_math import compute_shadow_ip


class CatalogShadowIP(JobHookReceiver):
    """Catalog a shadow IP for a real IP on create or update, and point primary_ip4 at it."""

    class Meta:
        """Declares the job hook's display name shown in Nautobot's job list."""

        name = "Catalog shadow IP on real IP create or update"

    def receive_job_hook(self, change, action, changed_object, snapshots=None):
        """React to a real IPAddress create/update by computing and cataloging its shadow IP.

        LIVE-VERIFIED on the lab server: Nautobot's JobHookReceiver.run()
        calls this with change=... as a keyword argument on this version,
        not change_context=... as the architecture doc's spec code assumed
        -- confirmed via a real TypeError: "CatalogShadowIP.receive_job_hook()
        got an unexpected keyword argument 'change'" the first time a real
        IPAddress creation actually triggered this hook. The parameter was
        never used in this method's body either way, so this is a pure
        rename, not a behavior change.

        LIVE-VERIFIED, a second signature bug found on the very next fresh
        "create" trigger tested (via onboarding-mcp, after the wizard's own
        earlier test device had already been created and only got its
        shadow IP via a manual backfill, never through a live create
        trigger with the fixed code): Nautobot doesn't always pass
        snapshots at all, raising TypeError: receive_job_hook() missing 1
        required positional argument: 'snapshots'. The method body already
        handled snapshots being falsy/None correctly (`(snapshots or {})`
        below) -- the only problem was the missing default value here.
        """
        if action not in ("create", "update"):
            return
        real_ip = changed_object
        if real_ip.namespace.name == "Global":
            return  # this is already a shadow record, never re-derive from itself

        if action == "update":
            before = (snapshots or {}).get("prechange", {}).get("host")
            after = (snapshots or {}).get("postchange", {}).get("host")
            if before == after:
                return  # some other field changed, not the address itself -- nothing to do

        real_prefix = real_ip.parent
        shadow_prefix_id = real_prefix.custom_field_data.get("nat_shadow_prefix")
        if not shadow_prefix_id:
            self.logger.warning(f"No shadow prefix mapped for {real_prefix} — skipping")
            return
        shadow_prefix = Prefix.objects.get(id=shadow_prefix_id)

        shadow_ip_str = compute_shadow_ip(str(real_ip.host), real_prefix, shadow_prefix)
        # LIVE-VERIFIED: ipam_ipaddress.status_id is NOT NULL on this
        # Nautobot version, same as ipam_prefix -- see site_onboarding.py's
        # note on the Prefix side of this same bug class. custom_field_data
        # is ALSO live-verified NOT settable via get_or_create()/create()'s
        # defaults/kwargs -- it's a Python property, not a real model field,
        # so Django's own field-name validation on object construction
        # rejects it (FieldError: "Invalid field name(s) for model
        # IPAddress: 'custom_field_data'"), the same underlying issue as
        # the _custom_field_data query-time fix in validate_vip_coverage.py,
        # just hit on the create path instead of filter(). Set it via the
        # property after creation and save(), matching how
        # site_onboarding.py already does this correctly for Prefix.
        new_shadow, _ = IPAddress.objects.get_or_create(
            address=f"{shadow_ip_str}/{shadow_prefix.prefix_length}",
            namespace=Namespace.objects.get(name="Global"),
            defaults={"status": Status.objects.get(name="Active")},
        )
        new_shadow.custom_field_data["real_ip"] = str(real_ip.host)
        new_shadow.save()

        real_ip.custom_field_data["mapped_shadow_ip"] = str(new_shadow.id)
        real_ip.save()

        # Deviation from the Shadow IP spec's literal `if real_ip.assigned_object:`
        # guard, made defensively rather than by assumption:
        # onboarding/nautobot_onboard_v2.py::set_primary_ip() DOES link the real
        # IP to a mgmt0 interface via the ipam/ip-address-to-interface endpoint
        # before setting primary_ip4 -- but that endpoint replaced IPAddress's
        # old direct assigned_object_type/assigned_object_id fields as of
        # Nautobot 3.1.3 per that function's own docstring, and PENDING LIVE
        # VERIFICATION, it's unconfirmed whether `assigned_object` still exists
        # as a plain attribute on this Nautobot version's IPAddress model, or
        # whether it reflects that through-table link if it does. Rather than
        # guess, check both, in order: the newer ip-address-to-interface
        # through-table (`real_ip.interfaces`), then the older
        # `assigned_object` attribute (via getattr, so a removed/renamed
        # attribute can't crash the hook), then -- for the create path only,
        # since it's how the real onboarding flow briefly links a device to
        # its IP before this hook fires -- a direct primary_ip4 lookup.
        device = None
        interfaces = getattr(real_ip, "interfaces", None)
        iface = interfaces.first() if interfaces is not None else None
        if iface is not None:
            device = iface.device
        else:
            assigned = getattr(real_ip, "assigned_object", None)
            if assigned is not None and hasattr(assigned, "device"):
                device = assigned.device
        if device is None and action == "create":
            device = Device.objects.filter(primary_ip4=real_ip).first()

        if device is not None:
            # On update, the device's OLD shadow IP is now stale -- deprecate
            # it rather than leaving an orphaned record with no device
            # pointing at it (mirrors ReconcileDeviceIPs' deprecation
            # pattern).
            if action == "update":
                old_shadow = device.primary_ip4
                if old_shadow and str(old_shadow.id) != str(new_shadow.id):
                    old_shadow.status = Status.objects.get(name="Deprecated")
                    old_shadow.save()
            device.primary_ip4 = new_shadow
            device.save()


register_jobs(CatalogShadowIP)
