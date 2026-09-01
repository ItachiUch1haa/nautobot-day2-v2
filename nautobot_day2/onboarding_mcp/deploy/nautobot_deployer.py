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


def deploy_device(device, tenant_id, site_id, namespace_id, real_prefix_id, real_prefix_cidr):
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
