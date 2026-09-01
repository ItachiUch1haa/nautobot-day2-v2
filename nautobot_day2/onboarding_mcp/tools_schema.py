"""
The 11 MCP tools from architecture doc §4, wired to the session state
machine, intake validators, controller adapters, and deploy modules.
server.py wraps each function below as an MCP tool (mirroring how
broker/api_server.py and broker/mcp_server.py both wrap broker/core.py's
shared logic) -- this module holds the actual behavior, not the MCP
transport plumbing.

Two schema gaps flagged, not silently papered over (same category as the
device-model gap already flagged in deploy/nautobot_deployer.py):
  - set_tenant's new-tenant path collects no tenant_group/industry_vertical
    (architecture doc §4's schema: {mode, tenant_name, tenant_slug?}) --
    creates a minimal Tenant (name/slug only, both fields are nullable
    on the real model) rather than reusing create_tenant.create_tenant_record()
    wholesale, which requires both.
  - set_site's new-site path triggers OnboardSite (shadow_ip/jobs/onboard_site_job.py)
    over REST, same as the existing wizard triggers SyncNetworkData -- but
    unlike that fire-and-forget trigger, set_site needs the Job's actual
    result (the real prefix's id/cidr) before it can transition to
    DEVICE_INTAKE, per the hard sequencing rule. PENDING LIVE VERIFICATION:
    the short poll-for-completion loop below assumes a Nautobot job-result
    response/polling shape not yet confirmed against a running server.

RESOLVED (was flagged as an open policy decision last pass): set_site's
new-site path now collects region/country/state/city/site_type -- the
same fields the web wizard's Step 1 collects -- and builds the site's
Location hierarchy itself via nautobot_onboard_v2.build_location_hierarchy(),
the exact same REST-based helper the wizard's /api/deploy calls (that
module is pure NautobotClient REST, no Django ORM, already imported
in-process by deploy/nautobot_deployer.py, so no Job-trigger-and-poll
detour is needed here the way OnboardSite needs one for the ORM-only
site_onboarding.onboard_site()). This makes onboarding-mcp's new-tenant+
new-site and existing-tenant+new-site paths behave the same way the web
wizard's do, shadow IP/VIP tracking included -- see set_site's own
docstring.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "controllers"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "intake"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy"))
_NAUTOBOT_DAY2_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _NAUTOBOT_DAY2_DIR)
sys.path.insert(0, os.path.join(_NAUTOBOT_DAY2_DIR, "onboarding"))

from client import NautobotClient  # noqa: E402
from session import state_machine  # noqa: E402
from static_device import StaticDeviceValidationError, validate_static_device  # noqa: E402
from ap_discovery import APDiscoveryError, select as select_aps, tag_candidates  # noqa: E402
from local_ssh_master import LocalSSHMasterClient  # noqa: E402
from meraki_client import MerakiClient  # noqa: E402
from mist_client import MistClient  # noqa: E402
from aruba_central_client import ArubaCentralClient  # noqa: E402
from other_generic import OtherGenericClient  # noqa: E402
from base import ControllerAuthError  # noqa: E402
import credential_writer  # noqa: E402
import nautobot_deployer  # noqa: E402
import nautobot_onboard_v2  # noqa: E402
import create_tenant as create_tenant_module  # noqa: E402

client = NautobotClient()
_store = None  # lazily connected — see _get_store()


def _get_store():
    global _store
    if _store is None:
        _store = state_machine.redis_store(state_machine.connect())
    return _store


_CONTROLLER_CLASSES = {
    "local_ssh_master": LocalSSHMasterClient,
    "meraki": MerakiClient,
    "mist": MistClient,
    "aruba_central": ArubaCentralClient,
    "other": OtherGenericClient,
}


class ToolError(Exception):
    """Raised by any tool handler on a client-facing error (bad input, illegal state, upstream failure)."""


def _session(session_id):
    return state_machine.OnboardingSession(_get_store(), session_id=session_id)


# ── start_onboarding / set_tenant / set_site ──────────────────────────────────

def _get_site_types():
    """
    Site-level location types (dcim.device content type, excluding the
    legacy 'Site' type) -- same query upload_app.py's /api/site-types uses
    to populate the wizard's dropdown. Returned so a conversational client
    can present the same choices a human sees in the wizard, instead of
    guessing a site_type string that build_location_hierarchy() will
    reject.
    """
    lts = client.get_all("dcim/location-types", params={"limit": 200})
    return sorted(
        lt["name"] for lt in lts
        if "dcim.device" in lt.get("content_types", []) and lt["name"] != "Site"
    )


def start_onboarding():
    """List existing tenants (customer namespaces) and start a new session."""
    session = state_machine.OnboardingSession.create(_get_store())
    session.transition("start_onboarding")
    tenants = client.get_all("tenancy/tenants", params={"limit": 200})
    return {
        "session_id": session.session_id,
        "state": state_machine.TENANT_RESOLUTION,
        "existing_tenants": [{"id": t["id"], "name": t["name"]} for t in tenants],
        "site_types": _get_site_types(),
    }


def set_tenant(session_id, mode, tenant_name, tenant_slug=None):
    """
    mode="existing": look up the tenant + list its sites.
    mode="new": create a minimal Tenant (name/slug only — see module
    docstring's flagged gap) + its IP namespace, matching
    create_tenant.py::create_namespace()'s naming convention (namespace
    name == tenant slug).
    """
    session = _session(session_id)
    if mode not in ("new", "existing"):
        raise ToolError("mode must be 'new' or 'existing'")

    if mode == "existing":
        found, obj = client.find_by_name("tenancy/tenants", tenant_name)
        if not found:
            raise ToolError(f"Tenant '{tenant_name}' not found")
        tenant = obj
        # Best-effort namespace resolution for an existing tenant: the
        # namespace was created with name == slug when this tenant was
        # first onboarded (create_tenant.py::create_namespace()'s
        # convention). Nautobot's Tenant object doesn't reliably expose a
        # 'slug' field across versions (removed in 3.1.3 per
        # nautobot_onboard_v2.py's own comment), so derive a candidate
        # via create_tenant.slugify() and confirm it actually exists;
        # fall back to the tenant's display name if not.
        candidate_slug = create_tenant_module.slugify(tenant_name)
        ns_found, ns_obj = client.find_by_name("ipam/namespaces", candidate_slug)
        namespace_name = candidate_slug if ns_found else tenant_name
        namespace_id = ns_obj["id"] if ns_found else None
        sites = client.get_all("dcim/locations", params={"tenant_id": tenant["id"], "limit": 200})
        tenant_state = {"id": tenant["id"], "name": tenant["name"], "namespace_name": namespace_name, "namespace_id": namespace_id}
        result = {"tenant": tenant_state, "sites": [{"id": s["id"], "name": s["name"]} for s in sites]}
    else:
        slug = tenant_slug or tenant_name.lower().replace(" ", "-")
        tenant, created = client.get_or_create("tenancy/tenants", tenant_name, {"name": tenant_name, "slug": slug})
        namespace, _ = client.get_or_create("ipam/namespaces", slug, {"name": slug, "description": f"IP namespace for {tenant_name}"})
        tenant_state = {"id": tenant["id"], "name": tenant_name, "namespace_name": slug, "namespace_id": namespace["id"]}
        result = {"tenant": tenant_state, "created": created, "sites": []}

    data = session.transition("set_tenant", lambda d: {**d, "tenant": tenant_state})
    result["state"] = data["state"]
    return result


def _get_job_log_entries(job_result_id):
    """
    Fetch a JobResult's log entries, trying both plausible REST shapes for
    this endpoint (a sub-resource action vs. the standalone job-log-entries
    collection filtered by job_result) and using whichever responds.

    LIVE-VERIFIED on the lab server, replacing an assumption that didn't
    hold: JobResult.status and .date_done never transition away from
    PENDING/None on this Nautobot deployment, even for a job that Celery's
    own log confirmed succeeded in under 2 seconds with the correct return
    value -- confirmed directly via nautobot-server shell, not guessed.
    So status can never be used as a completion signal here, for any job,
    regardless of how long a caller waits. job_log_entries, however, ARE
    reliably persisted and readable -- including Nautobot's own internal
    "Job completed" terminal log line on success, and any self.logger.error()
    message from a job that raised -- so those are the real signal to poll.
    """
    for url, params in (
        (f"extras/job-results/{job_result_id}/logs/", None),
        ("extras/job-log-entries/", {"job_result": job_result_id, "limit": 100}),
    ):
        try:
            r = client.get(url, params=params)
        except Exception:
            continue
        if r.ok:
            data = r.json()
            entries = data.get("results", data) if isinstance(data, dict) else data
            if isinstance(entries, list) and entries:
                return entries
    return []


def _wait_for_onboard_site(job_run_response, customer_ns_name, real_cidr, shadow_cidr, timeout_s=45, interval_s=0.5):
    """
    Poll OnboardSite's job log entries (see _get_job_log_entries) until a
    terminal signal appears, then look up the real/shadow Prefix objects it
    created directly by CIDR+namespace over REST, rather than trust
    JobResult.result -- that field showed the exact same never-persists
    symptom as .status on this deployment, so it can't be trusted either.
    """
    job_result_id = job_run_response.get("id") or job_run_response.get("job_result", {}).get("id")
    if not job_result_id:
        raise ToolError(f"Could not find a job-result id in the run response: {job_run_response}")

    deadline = time.time() + timeout_s
    completed = False
    while time.time() < deadline:
        entries = _get_job_log_entries(job_result_id)
        if entries:
            errors = [e for e in entries if (e.get("log_level") or e.get("level") or "").lower() in ("error", "critical")]
            if errors:
                raise ToolError(f"OnboardSite job failed: {errors[-1].get('message', errors[-1])}")
            if any("job completed" in (e.get("message") or "").lower() for e in entries):
                completed = True
                break
        time.sleep(interval_s)
    if not completed:
        raise ToolError(f"Timed out waiting for job result {job_result_id} to complete")

    # LIVE-VERIFIED: Nautobot's REST API returns a nested `namespace` on a
    # Prefix as a minimal {id, url, object_type} reference -- no "name" key
    # at all. Comparing p["namespace"]["name"] against "Global"/
    # customer_ns_name always evaluated to None == <string>, so this always
    # returned None for both ids, on every site ever onboarded through this
    # path (harmless so far since nothing downstream used them, but they're
    # meant to be real values). Resolve namespace ids once and compare by
    # id instead of by name.
    _, global_ns = client.find_by_name("ipam/namespaces", "Global")
    global_ns_id = global_ns["id"] if global_ns else None
    _, customer_ns = client.find_by_name("ipam/namespaces", customer_ns_name)
    customer_ns_id = customer_ns["id"] if customer_ns else None

    shadow_id = None
    shadow_lookup = client.get("ipam/prefixes", params={"prefix": shadow_cidr, "limit": 5})
    if shadow_lookup.ok:
        for p in shadow_lookup.json().get("results", []):
            if (p.get("namespace") or {}).get("id") == global_ns_id:
                shadow_id = p["id"]
                break

    real_id = None
    real_lookup = client.get("ipam/prefixes", params={"prefix": real_cidr, "limit": 5})
    if real_lookup.ok:
        for p in real_lookup.json().get("results", []):
            if (p.get("namespace") or {}).get("id") == customer_ns_id:
                real_id = p["id"]
                break

    return {
        "real_prefix_id": real_id,
        "real_prefix": real_cidr,
        "shadow_prefix_id": shadow_id,
        "shadow_prefix": shadow_cidr,
    }


def set_site(
    session_id,
    mode,
    site_name,
    region=None,
    country=None,
    state=None,
    city=None,
    site_type=None,
    real_cidr=None,
    shadow_cidr=None,
    fortigate_vdom=None,
    fortigate_vip_name=None,
    fortigate_tunnel_name=None,
):
    """
    mode="new": build the site's Location hierarchy (region/country/state/
    city/site_type — same required fields as the web wizard's Step 1,
    same nautobot_onboard_v2.build_location_hierarchy() call the wizard's
    /api/deploy makes, so a new site behaves identically whichever
    onboarding surface created it), then trigger OnboardSite (real+shadow
    prefix pair, via the shadow_ip module's REST-triggerable Job wrapper)
    and poll for completion before transitioning — the hard sequencing
    rule (§8) means DEVICE_INTAKE must not be reachable until this has
    actually finished and nat_shadow_prefix is confirmed populated, not
    just dispatched. fortigate_vdom/fortigate_vip_name/fortigate_tunnel_name
    are optional (VIP Management architecture doc §5/§6.5) — set them to
    also register this site for ValidateVIPCoverage reconciliation
    against the live FortiGate VIP object; omit them to onboard the
    shadow IP prefix pair without VIP tracking, same as before.
    mode="existing": confirm the site + its current real prefix (needed
    by add_static_device's mgmt_ip containment check).
    """
    session = _session(session_id)
    data = session.get_status()
    tenant = data.get("tenant")
    if not tenant:
        raise ToolError("No tenant set on this session — call set_tenant first")

    if mode == "new":
        missing = [
            f for f, v in
            (("region", region), ("country", country), ("state", state),
             ("city", city), ("site_type", site_type))
            if not v
        ]
        if missing:
            raise ToolError(f"Missing required field(s) for a new site: {', '.join(missing)}")
        if not real_cidr or not shadow_cidr:
            raise ToolError("real_cidr and shadow_cidr are required for a new site")

        # Lazy, once-per-process init, same pattern nautobot_deployer.deploy_device()
        # already uses for this exact module-level cache — harmless if deploy_site()
        # later finds it already populated.
        if not nautobot_onboard_v2._C:
            nautobot_onboard_v2.init_cache()
        active_status_id = nautobot_onboard_v2._C["statuses"].get("Active")
        if not active_status_id:
            raise ToolError("Nautobot 'Active' status not found — has bootstrap_nautobot.py been run?")

        loc_row = {
            "region": region, "country": country, "state": state,
            "city": city, "site_type": site_type, "site_name": site_name,
        }
        site_id, loc_log = nautobot_onboard_v2.build_location_hierarchy(loc_row, active_status_id, dry_run=False)
        if not site_id:
            raise ToolError(f"Could not create/find site Location: {loc_log}")

        jr = client.get("extras/jobs", params={"name": "Shadow IP: Onboard Site (real+shadow prefix pair)", "limit": 1})
        job_matches = jr.json().get("results", []) if jr.ok else []
        if not job_matches:
            raise ToolError("OnboardSite job not found — is it registered?")
        job = job_matches[0]
        if not job.get("enabled"):
            raise ToolError("OnboardSite job is registered but not enabled — see nautobot_deployer.trigger_sync's docstring for the fix")

        job_data = {
            "customer_ns_name": tenant.get("namespace_name") or tenant["name"],
            "site_name": site_name,
            "real_cidr": real_cidr,
            "shadow_cidr": shadow_cidr,
        }
        if fortigate_vdom:
            job_data["fortigate_vdom"] = fortigate_vdom
        if fortigate_vip_name:
            job_data["fortigate_vip_name"] = fortigate_vip_name
        if fortigate_tunnel_name:
            job_data["fortigate_tunnel_name"] = fortigate_tunnel_name

        run_resp = client.post(f"extras/jobs/{job['id']}/run", {"data": job_data})
        if not run_resp.ok:
            raise ToolError(f"FAILED triggering OnboardSite: {run_resp.status_code}: {run_resp.text[:160]}")

        onboard_result = _wait_for_onboard_site(
            run_resp.json(),
            customer_ns_name=job_data["customer_ns_name"],
            real_cidr=real_cidr,
            shadow_cidr=shadow_cidr,
        )
        site_state = {
            "id": None if str(site_id).startswith("DRY:") else site_id,
            "name": site_name,
            "real_prefix_id": onboard_result.get("real_prefix_id"),
            "real_prefix_cidr": onboard_result.get("real_prefix") or real_cidr,
            "shadow_prefix_id": onboard_result.get("shadow_prefix_id"),
        }
    else:
        found, loc = client.find_by_name("dcim/locations", site_name)
        if not found:
            raise ToolError(f"Site '{site_name}' not found")
        prefixes = client.get_all("ipam/prefixes", params={"location_id": loc["id"], "limit": 10})
        if not prefixes:
            raise ToolError(f"Site '{site_name}' has no real prefix on record")
        site_state = {
            "id": loc["id"],
            "name": site_name,
            "real_prefix_id": prefixes[0]["id"],
            "real_prefix_cidr": prefixes[0]["prefix"],
            "shadow_prefix_id": prefixes[0].get("custom_fields", {}).get("nat_shadow_prefix"),
        }

    data = session.transition("set_site", lambda d: {**d, "site": site_state})
    return {"site": site_state, "state": data["state"]}


# ── add_static_device ─────────────────────────────────────────────────────────

def add_static_device(session_id, role, vendor, hostname, mgmt_ip, credentials):
    """Validate and queue one static device (firewall/sdwan/switch — never role='ap', see intake/static_device.py)."""
    session = _session(session_id)
    data = session.get_status()
    site = data.get("site")
    if not site:
        raise ToolError("No site set on this session — call set_site first")

    try:
        validated = validate_static_device(role, vendor, hostname, mgmt_ip, site["real_prefix_cidr"])
    except StaticDeviceValidationError as e:
        raise ToolError(str(e))

    device_entry = {**validated, "credentials": credentials, "controller_managed": False}
    data = session.transition("add_static_device", lambda d: {**d, "pending_devices": d["pending_devices"] + [device_entry]})
    return {"queued": {k: v for k, v in device_entry.items() if k != "credentials"}, "pending_count": len(data["pending_devices"]), "state": data["state"]}


# ── set_ap_controller / scan_ap_controller / select_discovered_aps ───────────

def _build_controller(controller_type, fields):
    cls = _CONTROLLER_CLASSES.get(controller_type)
    if not cls:
        raise ToolError(f"Unknown controller_type '{controller_type}'")
    if controller_type == "other":
        return cls(fields.get("connection_type"), fields.get("config", {}))
    try:
        return cls(**fields)
    except TypeError as e:
        raise ToolError(f"Missing/unexpected fields for controller_type '{controller_type}': {e}")


def set_ap_controller(session_id, controller_type, **fields):
    """
    Build the controller adapter and test its connection immediately —
    don't let a bad token get discovered only at scan time (architecture
    doc §4). Rejects Cisco DNAC/WLC-SSH explicitly (architecture doc §6,
    master doc §9 item 7 — intentionally unimplemented) rather than
    silently falling through to another adapter.
    """
    if str(fields.get("platform", "")).lower() in ("cisco_dnac", "cisco_wlc", "dnac", "wlc-ssh"):
        raise ToolError("Cisco AP controller management (DNAC/WLC-SSH) is not yet implemented — see master doc §9 item 7")

    session = _session(session_id)
    controller = _build_controller(controller_type, fields)
    try:
        controller.test_connection()
    except (ControllerAuthError, NotImplementedError) as e:
        raise ToolError(f"Controller connection failed: {e}")

    pending_controller = {"controller_type": controller_type, "fields": fields}
    data = session.transition("set_ap_controller", lambda d: {**d, "pending_controller": pending_controller})
    return {"controller_type": controller_type, "connection_ok": True, "state": data["state"]}


def scan_ap_controller(session_id, filters=None):
    """Re-runnable (§4): does not clear anything already select_discovered_aps'd in this session."""
    session = _session(session_id)
    data = session.get_status()
    pc = data.get("pending_controller")
    if not pc:
        raise ToolError("No controller set on this session — call set_ap_controller first")

    controller = _build_controller(pc["controller_type"], pc["fields"])
    raw_candidates = controller.discover_aps(filters or {})
    tagged = tag_candidates(raw_candidates)

    data = session.transition("scan_ap_controller", lambda d: {**d, "last_scan_candidates": tagged})
    return {"candidates": [{k: v for k, v in c.items() if k != "raw"} for c in tagged], "state": data["state"]}


def select_discovered_aps(session_id, ap_ids):
    """Queue selected APs (controller_managed=True) — confirmed decision: preserves any already-selected APs across a re-scan."""
    session = _session(session_id)
    data = session.get_status()
    scanned = data.get("last_scan_candidates") or []
    pc = data["pending_controller"]
    controller_ref = f"{pc['controller_type']}-controller"

    try:
        selected = select_aps(scanned, ap_ids, controller_ref)
    except APDiscoveryError as e:
        raise ToolError(str(e))

    data = session.transition("select_discovered_aps", lambda d: {**d, "pending_devices": d["pending_devices"] + selected})
    return {"queued": selected, "pending_count": len(data["pending_devices"]), "state": data["state"]}


# ── review_pending_batch / remove_pending_device ──────────────────────────────

def review_pending_batch(session_id):
    """Show the full pending batch: static devices + selected APs + controller(s)."""
    session = _session(session_id)
    data = session.transition("review_pending_batch")
    return {
        "pending_devices": [{k: v for k, v in d.items() if k != "credentials"} for d in data["pending_devices"]],
        "pending_controller": data.get("pending_controller"),
        "state": data["state"],
    }


def remove_pending_device(session_id, pending_index):
    """Remove one item from the pending batch before deploy, by its index in review_pending_batch's list."""
    session = _session(session_id)

    def _remove(d):
        devices = list(d["pending_devices"])
        if pending_index < 0 or pending_index >= len(devices):
            raise ToolError(f"pending_index {pending_index} out of range (0..{len(devices) - 1})")
        devices.pop(pending_index)
        return {**d, "pending_devices": devices}

    data = session.transition("remove_pending_device", _remove)
    return {"pending_count": len(data["pending_devices"]), "state": data["state"]}


# ── deploy_site ────────────────────────────────────────────────────────────────

def deploy_site(session_id):
    """
    Write credentials, create Nautobot objects, trigger sync (architecture
    doc §9). Never sets primary_ip4 — CatalogShadowIP owns that. Returns a
    per-device status list using the wizard's existing ready/test-result
    vocabulary where applicable.
    """
    session = _session(session_id)
    data = session.get_status()
    tenant, site, pending_devices, pending_controller = data["tenant"], data["site"], data["pending_devices"], data.get("pending_controller")
    if not tenant or not site:
        raise ToolError("Session is missing tenant/site — cannot deploy")

    tenant_slug = tenant.get("namespace_name") or tenant["name"]

    cred_statuses = []
    try:
        cred_statuses.extend(credential_writer.write_static_device_credentials(tenant_slug, pending_devices))
    except credential_writer.CredentialWriteError as e:
        raise ToolError(f"Credential write failed: {e}")

    if pending_controller and any(d.get("controller_managed") for d in pending_devices):
        cred_statuses.append(credential_writer.write_controller_credentials(
            tenant_slug, pending_controller["controller_type"], pending_controller["fields"]
        ))

    device_statuses = []
    for device in pending_devices:
        try:
            result = nautobot_deployer.deploy_device(
                device, tenant["id"], site["id"],
                namespace_id=tenant.get("namespace_id"),
                real_prefix_id=site["real_prefix_id"],
                real_prefix_cidr=site["real_prefix_cidr"],
            )
            device_statuses.append(result)
        except nautobot_deployer.DeployError as e:
            device_statuses.append({"hostname": device.get("hostname") or device.get("name"), "status": f"FAILED: {e}"})

    try:
        sync_result = nautobot_deployer.trigger_sync(tenant["id"], site["id"])
        sync_status = {"triggered": True, "job_result": sync_result}
    except nautobot_deployer.DeployError as e:
        sync_status = {"triggered": False, "error": str(e)}

    data = session.transition("deploy_site")
    return {
        "state": data["state"],
        "credentials": cred_statuses,
        "devices": device_statuses,
        "sync": sync_status,
        "note": (
            "Shadow IPs will appear once CatalogShadowIP's Job Hook fires. "
            "Controller-discovered APs with no IP yet will pick one up on "
            "ReconcileDeviceIPs' next scheduled run."
        ),
    }


# ── get_session_status ────────────────────────────────────────────────────────

def get_session_status(session_id):
    """Current state + full pending batch, for reconnect. No state transition."""
    data = _session(session_id).get_status()
    return {**data, "pending_devices": [{k: v for k, v in d.items() if k != "credentials"} for d in data["pending_devices"]]}
