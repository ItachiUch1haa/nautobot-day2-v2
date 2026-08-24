"""
nautobot_day2.shadow_ip
Shadow IP mapping system: catalogs the real-to-shadow (RFC 6598,
100.64.0.0/10) IP correspondence that the FortiGate NVA's one-to-one NAT
already establishes on the network. This package only records that
mapping in Nautobot and keeps it in sync -- it never creates or changes
the NAT itself.
"""
