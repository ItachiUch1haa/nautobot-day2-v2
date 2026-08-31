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


def range_from_prefix(cidr):
    """Return a prefix's usable host range as 'first-last', in the same string
    shape FortiOS reports extip/mappedip -- used to compare a Nautobot Prefix
    against a live VIP object in ValidateVIPCoverage."""
    net = ipaddress.ip_network(cidr, strict=False)
    hosts = list(net.hosts())
    return f"{hosts[0]}-{hosts[-1]}"


def range_size(range_str):
    """Return the number of addresses spanned by a 'first-last' range string."""
    start, end = range_str.split("-")
    return int(ipaddress.ip_address(end.strip())) - int(ipaddress.ip_address(start.strip())) + 1
