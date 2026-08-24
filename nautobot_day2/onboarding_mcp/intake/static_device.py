"""
add_static_device validation (architecture doc §6): role/vendor against
the existing taxonomy, mgmt_ip containment against the site's real
prefix. Reuses onboarding/nautobot_prepare.py's normalize_role() (so a
role alias like "firewall" or "acc-sw" resolves the same way here as it
does in the CSV-based wizard path) and onboarding/vendor_matrix.py's
get_enabled_vendors() -- not a second, hand-maintained taxonomy.

Confirmed decision: role="ap" is rejected outright here -- APs can only
be onboarded via set_ap_controller -> discovery (see
intake/ap_discovery.py), never as a static device.
"""
import ipaddress
import os
import sys

_ONBOARDING_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "onboarding")
sys.path.insert(0, _ONBOARDING_DIR)
from nautobot_prepare import normalize_role, normalize_vendor  # noqa: E402
from vendor_matrix import get_enabled_vendors  # noqa: E402


class StaticDeviceValidationError(Exception):
    """Raised when add_static_device's input fails validation before being queued."""


def validate_static_device(role, vendor, hostname, mgmt_ip, real_prefix_cidr):
    """
    Validate one add_static_device call. Returns the normalized
    {role, vendor, hostname, mgmt_ip} dict on success, or raises
    StaticDeviceValidationError with a clear message.
    """
    normalized_role = normalize_role(role)
    if normalized_role is None:
        raise StaticDeviceValidationError(f"Unrecognized role '{role}' — not in the existing role taxonomy")
    if normalized_role == "ap":
        raise StaticDeviceValidationError(
            "role='ap' is not accepted by add_static_device — APs must be onboarded "
            "via set_ap_controller and discovery, not as a static device."
        )

    normalized_vendor = normalize_vendor(vendor)
    enabled_vendors = get_enabled_vendors()
    if normalized_vendor is None or normalized_vendor not in enabled_vendors:
        raise StaticDeviceValidationError(
            f"Unrecognized or disabled vendor '{vendor}' — enabled vendors: {sorted(enabled_vendors)}"
        )

    if not hostname or not hostname.strip():
        raise StaticDeviceValidationError("hostname is required")

    try:
        ip = ipaddress.ip_address(mgmt_ip)
    except ValueError:
        raise StaticDeviceValidationError(f"mgmt_ip '{mgmt_ip}' is not a valid IP address")

    real_net = ipaddress.ip_network(real_prefix_cidr, strict=False)
    if ip not in real_net:
        raise StaticDeviceValidationError(
            f"mgmt_ip {mgmt_ip} is outside the site's real prefix {real_prefix_cidr} — "
            f"double-check this isn't a typo'd IP from a different customer's range."
        )

    return {
        "role": normalized_role,
        "vendor": normalized_vendor,
        "hostname": hostname.strip(),
        "mgmt_ip": mgmt_ip,
    }
