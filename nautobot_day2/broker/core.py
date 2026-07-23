"""
nautobot_day2/broker/core.py

Shared diagnostic logic for the Agent Broker, used by both the REST API
and MCP server wrappers. Nautobot is queried read-only for device
metadata; OpenBao is the sole source of credentials; the broker itself
is the only thing that ever authenticates to a device.
"""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "onboarding"))
from sync_network_data import resolve_vendor, get_yaml_block, resolve_creds, _aruba_central_get_token

from client import NautobotClient
from openbao_client import fetch_openbao_secret


def get_device_context(device_name):
    """
    Look up a device in Nautobot by name.
    Returns a dict with tenant, platform, secrets_group, primary_ip —
    or raises if the device isn't found or has no tenant assigned.
    """
    client = NautobotClient()
    found, device = client.find_by_name("dcim/devices", device_name)

    if not found:
        raise Exception(f"DEVICE_NOT_FOUND: no device named '{device_name}' in Nautobot")

    device_tenant = device.get("tenant") or {}
    if not device_tenant:
        raise Exception(f"DEVICE_TENANT_MISSING: '{device_name}' has no tenant assigned")

    tenant_resp = client.get_absolute(device_tenant["url"])
    if not tenant_resp.ok:
        raise Exception(f"TENANT_LOOKUP_FAILED: could not resolve tenant for '{device_name}'")
    tenant_name = tenant_resp.json().get("name", "")
    tenant_slug = tenant_name.lower()

    ip_address = None
    primary_ip4 = device.get("primary_ip4")
    if primary_ip4:
        ip_resp = client.get_absolute(primary_ip4["url"])
        if ip_resp.ok:
            ip_address = ip_resp.json().get("address", "").split("/")[0]

    sg_name = None
    secrets_group = device.get("secrets_group")
    if secrets_group:
        sg_resp = client.get_absolute(secrets_group["url"])
        if sg_resp.ok:
            sg_name = sg_resp.json().get("name")

    platform_name = None
    platform_slug = None
    platform = device.get("platform") or {}
    if platform:
        plat_resp = client.get_absolute(platform["url"])
        if plat_resp.ok:
            plat_data = plat_resp.json()
            platform_name = plat_data.get("name")
            ns = plat_data.get("natural_slug", "")
            # Same derivation as sync_network_data.py: strip trailing
            # 4-char hash suffix from natural_slug (e.g. "fortios_9d1d"
            # -> "fortios"). The display `name` field is NOT reliable
            # for this (e.g. "ArubaOS AP" vs the real slug "arubaos-ap").
            platform_slug = ns.rsplit("_", 1)[0] if "_" in ns and len(ns.rsplit("_", 1)[-1]) == 4 else ns

    role_name = None
    role = device.get("role") or {}
    if role:
        role_resp = client.get_absolute(role["url"])
        if role_resp.ok:
            role_name = role_resp.json().get("name")

    return {
        "device_name": device_name,
        "tenant_name": tenant_name,
        "tenant_slug": tenant_slug,
        "ip_address": ip_address,
        "secrets_group": sg_name,
        "platform": platform_name,
        "platform_slug": platform_slug,
        "role": role_name,
        "device_id": device["id"],
    }


def fetch_device_credential(device_context):
    """
    Given the dict returned by get_device_context(), fetch the matching
    credential from OpenBao. secrets_group name must map to a KV path
    suffix the same way sync_network_data.py already derives it
    (secrets_group name minus the '-<tenant>' suffix).
    """
    sg_name = device_context["secrets_group"]
    tenant_slug = device_context["tenant_slug"]
    if not sg_name:
        raise Exception(f"NO_SECRETS_GROUP: '{device_context['device_name']}' has no secrets_group assigned")

    suffix_to_strip = f"-{tenant_slug}"
    if sg_name.endswith(suffix_to_strip):
        path_suffix = sg_name[: -len(suffix_to_strip)]
    else:
        path_suffix = sg_name

    return fetch_openbao_secret(tenant_slug, path_suffix)


def run_diagnostic_command(device_name, command):
    """
    Full pipeline: look up device -> fetch credential -> resolve vendor
    connection type -> dispatch via Netmiko -> return raw output.
    No command allowlist, no restricted-account enforcement (explicit
    decision — see project changelog). Any command string is accepted
    and run with whatever credential is stored for this device/tenant.
    """
    ctx = get_device_context(device_name)

    platform_slug = ctx.get("platform_slug") or ""
    role_name = ctx.get("role") or ""
    sg_name = ctx.get("secrets_group") or ""
    tenant_slug = ctx.get("tenant_slug") or ""

    section, yaml_key = resolve_vendor(platform_slug, role_name, sg_name)
    if not yaml_key:
        raise Exception(f"VENDOR_UNRESOLVED: could not determine connection type for '{device_name}' (platform={platform_slug}, role={role_name})")

    yaml_block = get_yaml_block(section, yaml_key)
    if not yaml_block:
        raise Exception(f"YAML_BLOCK_MISSING: no vendor_commands.yaml entry for {section}/{yaml_key}")

    # Same credential resolver the sync engine uses -- returns friendly
    # keys (user/password, or client_id/client_secret/base_url/token
    # depending on vendor) rather than raw OpenBao env-var-style keys.
    creds = resolve_creds(sg_name, tenant_slug)

    # Detect API-request-shaped command ({"method": "GET", "path": "..."})
    # vs a plain SSH CLI string, per the "generic passthrough" design
    # decision -- API vendors don't take arbitrary CLI strings, they take
    # a method+path against a REST endpoint.
    api_request = None
    if isinstance(command, dict):
        api_request = command
    elif isinstance(command, str):
        try:
            parsed = json.loads(command)
            if isinstance(parsed, dict) and "path" in parsed:
                api_request = parsed
        except (json.JSONDecodeError, ValueError):
            pass

    api_type = yaml_block.get("api_type")

    if api_type and api_request:
        return _dispatch_api(device_name, api_type, creds, api_request, tenant_slug)

    if api_type and not api_request:
        raise Exception(
            f"API_COMMAND_REQUIRED: '{device_name}' is an API-managed device "
            f"({api_type}) -- command must be a JSON object like "
            f'{{"method": "GET", "path": "/monitoring/v2/aps"}}, not a plain CLI string.'
        )

    netmiko_device_type = yaml_block.get("netmiko_device_type")
    if not netmiko_device_type:
        raise Exception(f"NO_NETMIKO_TYPE: {section}/{yaml_key} has no netmiko_device_type and no api_type -- unsupported vendor block")

    if not ctx.get("ip_address"):
        raise Exception(f"NO_IP: '{device_name}' has no primary IP set in Nautobot")

    username = creds.get("user")
    password = creds.get("password")

    if not username or not password:
        raise Exception(f"CREDENTIAL_INCOMPLETE: resolve_creds() returned no user/password for '{device_name}' (keys present: {list(creds.keys())})")

    from nornir.core import Nornir
    from nornir.core.inventory import Inventory, Host
    from nornir.plugins.runners import ThreadedRunner
    from nornir.core.plugins.connections import ConnectionPluginRegister
    from nornir_netmiko.tasks import netmiko_send_command

    # Connection plugins (netmiko, napalm, etc.) are normally auto-
    # discovered by InitNornir() via entry points. Since we build the
    # Nornir object directly (no config files, per-request in-memory
    # inventory), that discovery never runs unless triggered explicitly.
    # auto_register() is idempotent -- safe to call on every request.
    ConnectionPluginRegister.auto_register()

    host = Host(
        name=device_name,
        hostname=ctx["ip_address"],
        username=username,
        password=password,
        platform=netmiko_device_type,
    )
    inv = Inventory(hosts={device_name: host})
    # Single device per request -> 1 worker is correct; ThreadedRunner
    # is Nornir's default but must be registered explicitly since we
    # build the Nornir object directly rather than via InitNornir().
    nr = Nornir(inventory=inv, runner=ThreadedRunner(num_workers=1))

    result = nr.run(
        task=netmiko_send_command,
        command_string=command,
        use_timing=True,
        delay_factor=2,
        strip_prompt=True,
        strip_command=True,
    )

    host_result = result[device_name]
    # NOTE: MultiResult.failed aggregates every subtask attempt including
    # retried-and-recovered ones -- check the top-level task result
    # (index 0) specifically, per this project's own documented Nornir
    # lesson, not the aggregate .failed property.
    if host_result[0].failed:
        raise Exception(f"NORNIR_DISPATCH_FAILED: {host_result[0].exception}")

    return host_result[0].result


def _dispatch_api(device_name, api_type, creds, api_request, tenant_slug=''):
    """
    Dispatch a generic {"method": ..., "path": ...} request against an
    API-managed device's cloud controller (Mist or Aruba Central),
    via a Nornir task -- kept consistent with the SSH dispatch path
    rather than bypassing Nornir just because this is stateless HTTP.
    """
    from nornir.core import Nornir
    from nornir.core.inventory import Inventory, Host
    from nornir.plugins.runners import ThreadedRunner
    from nornir.core.plugins.connections import ConnectionPluginRegister
    from nornir.core.task import Result

    method = api_request.get("method", "GET").upper()
    path = api_request["path"]

    def _api_task(task):
        import requests as _requests

        if api_type == "juniper_mist":
            base_url = creds.get("base_url") or "https://api.mist.com"
            token = creds.get("token", "")
            headers = {"Authorization": f"Token {token}"}
        elif api_type == "aruba_central":
            base_url = creds.get("base_url", "")
            access_token = _aruba_central_get_token(creds, tenant_slug)
            headers = {"Authorization": f"Bearer {access_token}"}
        else:
            raise Exception(f"UNSUPPORTED_API_TYPE: '{api_type}' has no dispatch handler")

        resp = _requests.request(method, f"{base_url}{path}", headers=headers, timeout=15)
        if not resp.ok:
            raise Exception(f"API_ERROR: {resp.status_code} {resp.text[:300]}")
        return Result(host=task.host, result=resp.json())

    ConnectionPluginRegister.auto_register()
    host = Host(name=device_name)
    inv = Inventory(hosts={device_name: host})
    nr = Nornir(inventory=inv, runner=ThreadedRunner(num_workers=1))

    result = nr.run(task=_api_task)
    host_result = result[device_name]
    if host_result[0].failed:
        raise Exception(f"NORNIR_API_DISPATCH_FAILED: {host_result[0].exception}")

    return host_result[0].result
