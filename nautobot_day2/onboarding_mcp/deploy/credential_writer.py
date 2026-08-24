"""
deploy_site's credential-writing step (architecture doc §7, §9). Reuses
existing, working code rather than re-deriving anything:

  - onboarding/create_tenant.py's derive_objects() / create_secrets_groups()
    for static devices -- these become real Nautobot Devices with a
    secrets_group FK, so the same SecretsGroup + Secret + Association
    objects the wizard already creates need to exist here too.
  - openbao_client.update_rotated_credential() for the actual secret
    values -- it's already a safe read-merge-write, works fine for a
    first write too (current_data is {} on 404).

Controller credentials (architecture doc §7) are NOT modeled as Nautobot
SecretsGroups at all -- a controller isn't a Device in this pipeline, it's
adjacent infrastructure -- so those go straight to OpenBao under the
kv/data/tenants/<slug>/<vendor>-controller naming convention, no
SecretsGroup/Secret/Association objects involved. Same three pinned
AppRoles as everything else; no new Secret ID, no new AppRole.
"""
import os
import sys

_ONBOARDING_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "onboarding")
sys.path.insert(0, _ONBOARDING_DIR)
sys.path.insert(0, os.path.dirname(_ONBOARDING_DIR))
import create_tenant  # noqa: E402
from nautobot_prepare import vendor_to_device_type  # noqa: E402
from vendor_matrix import get_secrets_group_prefix  # noqa: E402
from openbao_client import update_rotated_credential  # noqa: E402


class CredentialWriteError(Exception):
    """Raised when a static device or controller credential can't be resolved/written."""


def _static_device_access_method(vendor):
    """
    Static devices (firewall/sdwan/switch) collected via add_static_device
    only ever provide plain SSH credentials (architecture doc §4's
    add_static_device schema: username/password_or_key, no managed_by
    field) -- so the access method is always this vendor's SSH method,
    matching vendor_commands.yaml's naming convention (aruba-ssh,
    fortinet-ssh, juniper-ssh, cisco-ssh, ...).
    """
    return f"{vendor}-ssh"


def write_static_device_credentials(tenant_slug, pending_devices, dry_run=False):
    """
    Ensure each pending static device's SecretsGroup exists (via
    create_tenant.py's existing derive/create logic) and write its
    username/password_or_key to OpenBao under that group's derived path.
    Returns a list of per-device {hostname, secrets_group, status} dicts.
    """
    static_devices = [d for d in pending_devices if not d.get("controller_managed")]
    if not static_devices:
        return []

    selections = {}
    for device in static_devices:
        dtype = vendor_to_device_type(device["vendor"], device["role"])
        if not dtype:
            raise CredentialWriteError(f"Cannot map role '{device['role']}' to a device type for vendor '{device['vendor']}'")
        access_method = _static_device_access_method(device["vendor"])
        selections.setdefault(device["vendor"], {}).setdefault(dtype, [])
        if access_method not in selections[device["vendor"]][dtype]:
            selections[device["vendor"]][dtype].append(access_method)

    # Only `slug` and `selections` are actually used by derive_objects()/
    # create_secrets_groups() -- deliberately not calling validate_profile()
    # here, since its required-field check (name/group/vertical) is about
    # the full run_create_tenant() flow (Tenant record creation), which
    # this credential-only path never touches.
    profile = {"slug": tenant_slug, "selections": selections}
    derived = create_tenant.derive_objects(profile)

    results = []
    create_tenant.create_secrets_groups(profile, derived, dry_run, results)

    suffix = tenant_slug.upper().replace("-", "_")
    statuses = []
    for device in static_devices:
        dtype = vendor_to_device_type(device["vendor"], device["role"])
        access_method = _static_device_access_method(device["vendor"])
        prefix = get_secrets_group_prefix(device["vendor"], dtype, access_method)
        if not prefix:
            statuses.append({"hostname": device["hostname"], "secrets_group": None, "status": f"FAILED: no secrets_group_prefix for {device['vendor']}/{dtype}/{access_method}"})
            continue
        secrets_group = f"{prefix}-{tenant_slug}"

        if dry_run:
            statuses.append({"hostname": device["hostname"], "secrets_group": secrets_group, "status": "would write"})
            continue

        creds = device.get("credentials", {})
        updates = {}
        if "username" in creds:
            updates[f"{prefix.upper().replace('-', '_')}_USER_{suffix}"] = creds["username"]
        if "password_or_key" in creds:
            updates[f"{prefix.upper().replace('-', '_')}_PASS_{suffix}"] = creds["password_or_key"]

        try:
            update_rotated_credential(tenant_slug, prefix, updates)
            statuses.append({"hostname": device["hostname"], "secrets_group": secrets_group, "status": "written"})
        except Exception as e:
            statuses.append({"hostname": device["hostname"], "secrets_group": secrets_group, "status": f"FAILED: {e}"})

    return statuses


def write_controller_credentials(tenant_slug, controller_type, credentials, dry_run=False):
    """
    Write a controller's credentials straight to OpenBao at
    kv/data/tenants/<slug>/<vendor>-controller -- no SecretsGroup object,
    per architecture doc §7. `credentials` is the exact field dict
    required_credential_fields() collected for this controller_type.
    """
    path_suffix = f"{controller_type}-controller"
    if dry_run:
        return {"path_suffix": path_suffix, "status": "would write"}
    try:
        update_rotated_credential(tenant_slug, path_suffix, credentials)
        return {"path_suffix": path_suffix, "status": "written"}
    except Exception as e:
        return {"path_suffix": path_suffix, "status": f"FAILED: {e}"}
