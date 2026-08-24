"""
Catalogs the shadow IP the moment a new real IP is created (via onboarding
device sync, onboarding_mcp's deploy step, or any future IP creation) --
this does not create anything on the network, only records the mapping
the FortiGate NVA's NAT already covers.

Job Hook configuration (Extensibility -> Job Hooks, a manual one-time
Nautobot-admin step, not something code can register): trigger on
IPAddress, action "create", scoped to any namespace except Global (to
avoid re-triggering on the shadow record itself).
"""
from nautobot.apps.jobs import JobHookReceiver, register_jobs
from nautobot.dcim.models import Device
from nautobot.ipam.models import IPAddress, Namespace, Prefix

from ..shadow_math import compute_shadow_ip


class CatalogShadowIP(JobHookReceiver):
    """Catalog a shadow IP for a newly created real IP, and point primary_ip4 at it."""

    class Meta:
        """Declares the job hook's display name shown in Nautobot's job list."""

        name = "Catalog shadow IP on new real IP"

    def receive_job_hook(self, change_context, action, changed_object, snapshots):
        """React to a real IPAddress creation by computing and cataloging its shadow IP."""
        if action != "create":
            return
        real_ip = changed_object
        if real_ip.namespace.name == "Global":
            return  # this is already a shadow record

        real_prefix = real_ip.parent
        shadow_prefix_id = real_prefix.custom_field_data.get("nat_shadow_prefix")
        if not shadow_prefix_id:
            self.logger.warning(f"No shadow prefix mapped for {real_prefix} — skipping")
            return
        shadow_prefix = Prefix.objects.get(id=shadow_prefix_id)

        shadow_ip_str = compute_shadow_ip(str(real_ip.host), real_prefix, shadow_prefix)
        shadow_ip, _ = IPAddress.objects.get_or_create(
            address=f"{shadow_ip_str}/{shadow_prefix.prefix_length}",
            namespace=Namespace.objects.get(name="Global"),
            defaults={"custom_field_data": {"real_ip": str(real_ip.host)}},
        )

        real_ip.custom_field_data["mapped_shadow_ip"] = str(shadow_ip.id)
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
        # guess, check both: the spec's original assigned_object condition
        # (via getattr, so a removed/renamed attribute can't crash the hook)
        # OR a direct primary_ip4 lookup, which is how the real onboarding flow
        # links a device to its IP regardless of what assigned_object resolves to.
        has_assigned_object = bool(getattr(real_ip, "assigned_object", None))
        devices_by_primary_ip = Device.objects.filter(primary_ip4=real_ip)
        if has_assigned_object or devices_by_primary_ip.exists():
            for device in devices_by_primary_ip:
                device.primary_ip4 = shadow_ip
                device.save()


register_jobs(CatalogShadowIP)
