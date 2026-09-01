"""
deploy_site's Nautobot object-creation step (architecture doc §7, §9).
Reuses onboarding/nautobot_onboard_v2.py's existing helpers (init_cache(),
get_or_create_device_type(), get_or_create_device()) the same way
chatops/worker.py already does (dynamically-loaded module, module-level
_C cache) rather than re-deriving manufacturer/device-type/role lookups.

LIVE-VERIFIED, architecture doc §9's original design reversed: this file
used to deliberately never set device.primary_ip4 (relying entirely on
CatalogShadowIP's "create" job hook to find the device via
real_ip.interfaces/assigned_object/a primary_ip4-already-set fallback,
to avoid two code paths racing to set the same field). Live testing via
onboarding-mcp's deploy_site() found this hook fires as a Celery task
queued off the IPAddress "create" signal, which reliably raced ahead of
this module's own follow-up ip-address-to-interface REST call --
confirmed by a real device whose shadow IP custom field got computed and
stored correctly (proving the hook ran and completed with no
interfaces/assigned_object/primary_ip4 match, i.e. `device` resolved to
None) while device.primary_ip4 stayed permanently None. Static devices
(this module's entire purpose) never get a second chance at this: unlike
controller-discovered APs, ReconcileDeviceIPs' DHCP-lease-drift check
doesn't apply to them (no MAC-keyed DHCP lease to match against), so a
lost race here is not self-healing.
Now calls nautobot_onboard_v2.set_primary_ip() -- all 3 of its steps,
same call the CSV wizard's onboard_devices() already makes -- so
device.primary_ip4 is set to the real IP synchronously within this same
deploy_device() call, giving CatalogShadowIP's "create"-path
Device.objects.filter(primary_ip4=real_ip) fallback a real row to find
regardless of how the interfaces-link race resolves. The hook still
immediately supersedes it with the shadow IP once it runs (exactly the
sequence already live-verified working end-to-end through the web
wizard) -- this isn't "two code paths racing", it's the same
briefly-real-then-shadow sequence the wizard always relied on.

GAP FLAGGED, NOT SILENTLY PAPERED OVER: architecture doc §4/§6's
add_static_device schema collects no hardware device model (unlike the
CSV wizard's 'model' column) -- there's no way to create a precise
DeviceType from what this tool actually receives. Uses a generic
"<Vendor Label> Generic" placeholder DeviceType per vendor until the
intake schema is extended with a real model field.

For controller-discovered APs with no IP yet at deploy time: creates the
Device/Interface and explicitly skips IPAddress creation --
ReconcileDeviceIPs already owns catching this on its next scheduled run
(architecture doc §8) -- no polling logic needed here.
"""
import ipaddress
import os
import sys

_NAUTOBOT_DAY2_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ONBOARDING_DIR = os.path.join(_NAUTOBOT_DAY2_DIR, "onboarding")
sys.path.insert(0, _NAUTOBOT_DAY2_DIR)
sys.path.insert(0, _ONBOARDING_DIR)
import nautobot_onboard_v2  # noqa: E402
from client import NautobotClient  # noqa: E402
from vendor_matrix import VENDOR_MATRIX, get_all_platforms, get_default_platform  # noqa: E402
from nautobot_prepare import vendor_to_device_type  # noqa: E402

client = NautobotClient()

# Canonical role name -> Nautobot Role display name. Nautobot's
# extras/roles are looked up by name; bootstrap_nautobot.py already
# creates roles matching these canonical strings for every role this
# pipeline knows about (branch-fw, access-switch, etc.) except the new
# sdwan-edge, which this build's INSTALL.md phase must remind an operator
# to create once (same one-time step as any other new role).
_ROLE_DISPLAY_NAME = {
    "branch-fw": "branch-fw",
    "access-switch": "access-switch",
    "core-switch": "core-switch",
    "distribution-switch": "distribution-switch",
    "wan-router": "wan-router",
    "sdwan-edge": "sdwan-edge",
}


class DeployError(Exception):
    """Raised when a queued device can't be created in Nautobot."""


def _resolve_platform_id(vendor, dtype):
    """Resolve this vendor+device_type combo's default platform to a Nautobot Platform id, by name."""
    platform_slug = get_default_platform(vendor, dtype)
    if not platform_slug:
        return None
    label = get_all_platforms().get(platform_slug, {}).get("label")
    if not label:
        return None
    return client.get_id_by_name("dcim/platforms", label)


def _link_shadow_ip_sync(device_id, real_ip_id, real_host, real_prefix_cidr, shadow_prefix_id, active_status_id):
    """
    REST-only mirror of CatalogShadowIP's core logic (shadow_ip/jobs/
    catalog_shadow_ip.py), called synchronously right after
    set_primary_ip() for a device this module itself just created.

    LIVE-VERIFIED this closes a race the reordering in deploy_device()
    alone did not: the Job Hook fires the instant get_or_create_ip()'s
    POST returns (queued off the IPAddress "create" signal), which is
    BEFORE set_primary_ip()'s own three follow-up REST calls even start
    -- confirmed live by a test device whose shadow IP got computed and
    its mapped_shadow_ip custom field got set correctly (proving the hook
    ran to completion) while device.primary_ip4 stayed on the real IP
    (proving the hook's device lookup found nothing, since neither the
    interface link nor primary_ip4 existed yet at that point no matter
    which order this module's own calls run in afterward). This tool
    (onboarding_mcp) only has REST access, not the Django ORM the hook
    itself uses, so this reimplements the same offset-preserving math
    (shadow_ip/shadow_math.py's compute_shadow_ip, inlined here since it
    needs only ipaddress, no ORM) and the same get-or-create-shadow-IP
    steps over REST, then sets primary_ip4 directly -- entirely
    bypassing the hook's racy device lookup for this path. Idempotent
    with the hook's own (separately still-running) attempt: whichever
    writes first wins, the other's writes are harmless repeats of the
    same values.

    LIVE-VERIFIED a further wrinkle in that idempotency claim, on the
    very next fresh device tested after this function was first added:
    "whichever writes first wins" assumed the loser's own pre-create GET
    lookup would simply find what the winner already committed -- but
    when this function's own GET and POST race directly against the
    hook's separate get_or_create() (both attempting to create the exact
    same shadow IPAddress row within the same narrow window), the loser
    can hit its POST *after* its own GET already ran clean (found
    nothing) but *after* the hook's create has since committed --
    yielding a real 400 IntegrityError-backed conflict ("IP address with
    this Parent and Host already exists"), not a duplicate creation.
    Handled the same way any other race-loser should be: on that specific
    409-shaped 400, re-fetch and use what the winner (the hook) created,
    instead of treating it as a hard failure.
    """
    shadow_prefix = client.get(f"ipam/prefixes/{shadow_prefix_id}/").json()
    shadow_net = ipaddress.ip_network(shadow_prefix["prefix"], strict=False)
    real_net = ipaddress.ip_network(real_prefix_cidr, strict=False)
    offset = int(ipaddress.ip_address(real_host)) - int(real_net.network_address)
    shadow_ip_str = str(ipaddress.ip_address(int(shadow_net.network_address) + offset))
    shadow_address = f"{shadow_ip_str}/{shadow_net.prefixlen}"

    global_ns_id = client.get_id_by_name("ipam/namespaces", "Global")
    if not global_ns_id:
        raise DeployError("Nautobot 'Global' namespace not found")

    def _find_existing_shadow():
        # LIVE-VERIFIED: filtering ipam/ip-addresses by ?address=<cidr> is
        # unreliable for finding a record that demonstrably exists --
        # confirmed live when this lookup found nothing immediately after
        # this function's own POST got a 400 uniqueness conflict against
        # that exact address (proving the row was there). Filtering by
        # ?parent=<shadow_prefix_id> (an exact id match) plus a plain
        # ?host= comparison (the bare IP, independent of whatever mask
        # the row happens to be stored with) is the same pattern that
        # reliably found records during this session's live debugging.
        existing = client.get("ipam/ip-addresses", params={"parent": shadow_prefix_id, "limit": 50})
        if not existing.ok:
            return None
        for obj in existing.json().get("results", []):
            if obj.get("host") == shadow_ip_str:
                return obj["id"]
        return None

    shadow_id = _find_existing_shadow()
    if not shadow_id:
        r = client.post("ipam/ip-addresses", {
            "address": shadow_address,
            "namespace": {"id": global_ns_id},
            "status": {"id": active_status_id},
            "type": "host",
        })
        if r.status_code == 201:
            shadow_id = r.json()["id"]
        elif r.status_code == 400 and "already exists" in r.text:
            shadow_id = _find_existing_shadow()
            if not shadow_id:
                raise DeployError(
                    f"FAILED creating shadow IP (lost race to the Job Hook, "
                    f"but re-fetch still found nothing): {r.status_code}: {r.text[:120]}"
                )
        else:
            raise DeployError(f"FAILED creating shadow IP: {r.status_code}: {r.text[:120]}")

    client.patch(f"ipam/ip-addresses/{shadow_id}/", {"custom_fields": {"real_ip": real_host}})
    client.patch(f"ipam/ip-addresses/{real_ip_id}/", {"custom_fields": {"mapped_shadow_ip": shadow_id}})

    r2 = client.patch(f"dcim/devices/{device_id}/", {"primary_ip4": {"id": shadow_id}})
    if not r2.ok:
        raise DeployError(f"FAILED setting device primary_ip4 to shadow IP: {r2.status_code}: {r2.text[:120]}")


def deploy_device(device, tenant_id, site_id, namespace_id, real_prefix_id, real_prefix_cidr, shadow_prefix_id=None):
    """
    Create one queued device (static or controller-managed AP) in
    Nautobot. Returns {hostname, device_id, ip_created, status}.
    """
    # get_or_create_device_type() reads nautobot_onboard_v2._C['manufacturers'],
    # populated only by init_cache() -- lazily call it once per process
    # rather than requiring every caller to remember to, or refetching it
    # for every single device in a batch deploy.
    if not nautobot_onboard_v2._C:
        nautobot_onboard_v2.init_cache()

    active_status_id = client.get_id_by_name("extras/statuses", "Active")
    if not active_status_id:
        raise DeployError("Nautobot 'Active' status not found — has bootstrap_nautobot.py been run?")

    vendor = device["vendor"]
    role = device["role"]
    dtype = vendor_to_device_type(vendor, role)
    if not dtype:
        raise DeployError(f"Cannot map role '{role}' to a device type for vendor '{vendor}'")

    vendor_label = VENDOR_MATRIX.get(vendor, {}).get("label", vendor)
    model = f"{vendor_label} Generic"  # see module docstring's flagged gap
    dt_id, dt_msg = nautobot_onboard_v2.get_or_create_device_type(model, vendor, False, {})
    if dt_msg.startswith("FAILED"):
        raise DeployError(f"device_type: {dt_msg}")

    role_name = _ROLE_DISPLAY_NAME.get(role, role)
    role_id = client.get_id_by_name("extras/roles", role_name)
    if not role_id:
        raise DeployError(f"Nautobot role '{role_name}' not found — create it once via bootstrap, same as any other role")

    platform_id = _resolve_platform_id(vendor, dtype)

    hostname = device.get("hostname") or device.get("name")
    row = {"device_name": hostname, "serial": ""}
    dev_id, dev_msg = nautobot_onboard_v2.get_or_create_device(
        row, site_id, dt_id, role_id, platform_id, tenant_id, active_status_id,
        sg_id=None, dry_run=False,
    )
    if dev_msg.startswith("FAILED") or dev_id is None:
        raise DeployError(f"device: {dev_msg}")

    mgmt_ip = device.get("mgmt_ip") or device.get("current_ip")
    ip_created = False
    if mgmt_ip:
        # LIVE-VERIFIED: passing the bare host mgmt_ip straight to
        # get_or_create_ip() (no mask) got Nautobot to silently store it
        # as a /32 host record instead of matching the site's real prefix
        # mask -- confirmed via a live device whose IP came back as
        # "10.99.4.10/32" against a 10.99.4.0/24 real prefix. The CSV
        # wizard's row['ip'] column already carries its own mask from the
        # uploaded CSV; this tool's add_static_device only ever collects a
        # bare mgmt_ip, so derive the mask from real_prefix_cidr instead.
        mask_length = ipaddress.ip_network(real_prefix_cidr, strict=False).prefixlen
        address = f"{mgmt_ip}/{mask_length}"
        pfx_id, _ = nautobot_onboard_v2.get_or_create_prefix(
            address, namespace_id, tenant_id, active_status_id, False, {}
        )
        ip_id, ip_msg = nautobot_onboard_v2.get_or_create_ip(
            address, namespace_id, tenant_id, active_status_id, pfx_id, False
        )
        if ip_msg.startswith("FAILED"):
            raise DeployError(f"ip: {ip_msg}")
        primary_msg = nautobot_onboard_v2.set_primary_ip(dev_id, ip_id, False)
        if "FAILED" in str(primary_msg):
            raise DeployError(f"primary_ip: {primary_msg}")
        if shadow_prefix_id:
            _link_shadow_ip_sync(dev_id, ip_id, mgmt_ip, real_prefix_cidr, shadow_prefix_id, active_status_id)
        ip_created = True
    # else: controller-managed AP with no IP yet at deploy time -- Device
    # created, IPAddress intentionally skipped; ReconcileDeviceIPs picks
    # it up once the controller's DHCP lease shows it (see module docstring).

    return {"hostname": hostname, "device_id": dev_id, "ip_created": ip_created, "status": dev_msg}


def trigger_sync(tenant_id, site_id, category="all", dry_run=False):
    """
    Trigger the SyncNetworkData Job the exact same way upload_app.py's
    /api/deploy already does: resolve tenant/site to their real Nautobot
    IDs (already have them here), resolve the Job by name, POST its
    /run/ endpoint. Checks enabled=True first (master doc §6 row 5 --
    Job.enabled defaults to False on fresh registration and /run/ 403s
    otherwise), which upload_app.py's own deploy step does not currently
    do proactively -- this build adds that check per the brief's Phase 6.
    """
    jr = client.get("extras/jobs", params={"name": "Sync Network Data", "limit": 1})
    job_results = jr.json().get("results", []) if jr.ok else []
    if not job_results:
        raise DeployError("'Sync Network Data' job not found — is it registered?")
    job = job_results[0]
    if not job.get("enabled"):
        raise DeployError(
            "'Sync Network Data' job is registered but not enabled — run "
            "Job.objects.filter(module_name__startswith='nautobot_day2')."
            "update(enabled=True) once, then retry deploy_site."
        )

    run_resp = client.post(f"extras/jobs/{job['id']}/run", {
        "data": {"tenant": tenant_id, "site": site_id, "category": category, "dry_run": dry_run}
    })
    if not run_resp.ok:
        raise DeployError(f"FAILED triggering sync: {run_resp.status_code}: {run_resp.text[:160]}")
    return run_resp.json()
