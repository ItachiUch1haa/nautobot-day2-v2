"""
FortiOS REST API client for the FortiGate NVA that performs the shadow-IP
NAT -- NOT one of the customer-premise FortiGate firewalls already
onboarded as devices via vendor_matrix.py (that's a separate, unrelated
concern). This is the shared, per-VDOM NVA that ReconcileDeviceIPs,
DiscoverNewDevices, and ValidateVIPCoverage read from.

PENDING LIVE VERIFICATION: built against FortiOS REST API's documented
shape (token-header auth, /api/v2/monitor and /api/v2/cmdb endpoints,
vdom as a query param), but not yet run against a real FortiGate NVA.
Confirm endpoint paths, auth header name, and response shape against the
actual lab appliance before relying on this in production.
"""
import os

import requests

_session = requests.Session()


def _base_url():
    base_url = os.environ.get("FORTIGATE_NVA_BASE_URL")
    if not base_url:
        raise Exception(
            "FORTIGATE_CONFIG_ERROR: FORTIGATE_NVA_BASE_URL not set in "
            "environment — cannot reach the FortiGate NVA."
        )
    return base_url.rstrip("/")


def _api_token():
    token = os.environ.get("FORTIGATE_NVA_API_TOKEN")
    if not token:
        raise Exception(
            "FORTIGATE_CONFIG_ERROR: FORTIGATE_NVA_API_TOKEN not set in "
            "environment — cannot authenticate to the FortiGate NVA."
        )
    return token


def get_dhcp_leases(vdom):
    """Return {mac: current_real_ip} for every active DHCP lease in the given VDOM."""
    try:
        resp = _session.get(
            f"{_base_url()}/api/v2/monitor/system/dhcp",
            params={"vdom": vdom},
            headers={"Authorization": f"Bearer {_api_token()}"},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        raise Exception(f"FORTIGATE_UNREACHABLE: could not fetch DHCP leases for vdom={vdom} — {e}")

    leases = {}
    for entry in resp.json().get("results", []):
        mac = entry.get("mac")
        ip = entry.get("ip")
        if mac and ip:
            leases[mac.lower()] = ip
    return leases


def get_vip(vdom, name):
    """Return the named static-NAT VIP object's extip/mappedip/type for the
    given VDOM, or None if no such VIP exists on the firewall. Used by
    ValidateVIPCoverage to reconcile Nautobot's recorded shadow/real prefix
    pair against what's actually configured.

    PENDING LIVE VERIFICATION: FortiOS's mappedip response shape for a
    static-nat VIP has varied across 7.2.x/7.4.x -- confirmed shape here is
    `mappedip: [{"range": "a.b.c.d-a.b.c.e"}]` per the architecture doc;
    verify against the real appliance before trusting this parsing.
    """
    try:
        resp = _session.get(
            f"{_base_url()}/api/v2/cmdb/firewall/vip/{name}",
            params={"vdom": vdom},
            headers={"Authorization": f"Bearer {_api_token()}"},
            timeout=15,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
    except Exception as e:
        raise Exception(f"FORTIGATE_UNREACHABLE: could not fetch VIP {name} for vdom={vdom} — {e}")

    results = resp.json().get("results", [])
    if not results:
        return None
    vip = results[0]
    mappedip = vip.get("mappedip")
    return {
        "extip": vip.get("extip"),
        "mappedip": mappedip[0].get("range") if mappedip else None,
        "type": vip.get("type"),
    }


def get_ippools(vdom):
    """Return a list of (start, end) address ranges for every firewall IP pool configured in the given VDOM."""
    try:
        resp = _session.get(
            f"{_base_url()}/api/v2/cmdb/firewall/ippool",
            params={"vdom": vdom},
            headers={"Authorization": f"Bearer {_api_token()}"},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        raise Exception(f"FORTIGATE_UNREACHABLE: could not fetch IP pools for vdom={vdom} — {e}")

    pools = []
    for entry in resp.json().get("results", []):
        start = entry.get("startip")
        end = entry.get("endip")
        if start and end:
            pools.append((start, end))
    return pools
