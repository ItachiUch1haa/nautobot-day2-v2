"""
Hook point for the existing per-site onboarding flow: given a site's real
subnet and its paired shadow subnet, ensure the real Prefix, the shadow
Prefix, and the nat_shadow_prefix link between them exist before any
device sync runs. Runs inside a Django/Nautobot process (ORM access) --
called from OnboardSite (jobs/onboard_site_job.py) so it's reachable over
REST the same way SyncNetworkData already is, not imported directly by
the standalone onboarding_mcp process.
"""
import ipaddress

from nautobot.dcim.models import Location
from nautobot.ipam.models import Namespace, Prefix


class ShadowIPValidationError(Exception):
    """Raised when a real/shadow CIDR pair fails validation before onboarding a site."""


def onboard_site(customer_ns_name, site_name, real_cidr, shadow_cidr):
    """Call this once per site as part of the existing onboarding flow, before device import."""
    real_net = ipaddress.ip_network(real_cidr, strict=False)
    shadow_net = ipaddress.ip_network(shadow_cidr, strict=False)
    if real_net.prefixlen != shadow_net.prefixlen:
        raise ShadowIPValidationError(
            f"real_cidr {real_cidr} and shadow_cidr {shadow_cidr} have different "
            f"prefix lengths ({real_net.prefixlen} vs {shadow_net.prefixlen}) -- "
            f"the shadow prefix must be the same length as the real prefix."
        )

    global_ns = Namespace.objects.get(name="Global")
    if Prefix.objects.filter(namespace=global_ns).net_overlap(shadow_cidr).exists():
        raise ShadowIPValidationError(
            f"shadow_cidr {shadow_cidr} overlaps an existing shadow Prefix in the "
            f"Global namespace -- shadow blocks must be unique across every customer."
        )

    customer_ns = Namespace.objects.get(name=customer_ns_name)
    location = Location.objects.get(name=site_name, parent__name=customer_ns_name)

    shadow_prefix, _ = Prefix.objects.get_or_create(
        prefix=shadow_cidr, namespace=global_ns,
    )
    real_prefix, _ = Prefix.objects.get_or_create(
        prefix=real_cidr, namespace=customer_ns, location=location,
    )
    real_prefix.custom_field_data["nat_shadow_prefix"] = str(shadow_prefix.id)
    real_prefix.save()
    return real_prefix, shadow_prefix
