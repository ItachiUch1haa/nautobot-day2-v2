"""
Offset-preserving shadow IP calculation -- the only math in the shadow IP
system. Works because NAT is configured as one-to-one (host-bit-preserving)
on the FortiGate NVA: a real IP's offset from its prefix's network address
is applied unchanged to the shadow prefix's network address, and vice
versa. Pure arithmetic against Nautobot's own Prefix records -- no network
call, no external lookup.
"""
import ipaddress


def compute_shadow_ip(real_ip_str, real_prefix, shadow_prefix):
    """Return the shadow IP that corresponds to a real IP, given the real and shadow Prefix records."""
    real_net = ipaddress.ip_network(real_prefix.prefix, strict=False)
    shadow_net = ipaddress.ip_network(shadow_prefix.prefix, strict=False)
    offset = int(ipaddress.ip_address(real_ip_str)) - int(real_net.network_address)
    return str(ipaddress.ip_address(int(shadow_net.network_address) + offset))


def compute_real_ip(shadow_ip_str, real_prefix, shadow_prefix):
    """Inverse of compute_shadow_ip -- used by the reconciliation job's reverse lookups."""
    real_net = ipaddress.ip_network(real_prefix.prefix, strict=False)
    shadow_net = ipaddress.ip_network(shadow_prefix.prefix, strict=False)
    offset = int(ipaddress.ip_address(shadow_ip_str)) - int(shadow_net.network_address)
    return str(ipaddress.ip_address(int(real_net.network_address) + offset))
