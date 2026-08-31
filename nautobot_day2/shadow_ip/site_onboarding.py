"""
Hook point for the existing per-site onboarding flow: given a site's real
subnet and its paired shadow subnet, ensure the real Prefix, the shadow
Prefix, and the nat_shadow_prefix link between them exist before any
device sync runs. Runs inside a Django/Nautobot process (ORM access) --
called from OnboardSite (jobs/onboard_site_job.py) so it's reachable over
REST the same way SyncNetworkData already is, not imported directly by
the standalone onboarding_mcp process.

FLAGGED CONFLICT, resolved here rather than left silently broken: the VIP
Management architecture doc's §3.2 assumes a 2-level Location model ("one
Location per customer, one child Location per site" -- i.e. the site's
parent is named after the customer). This codebase's REAL Location model
(bootstrap_nautobot.py's LOCATION_TYPES, built by
onboarding/nautobot_onboard_v2.py::build_location_hierarchy()) is a
5-level Region -> Country -> State -> City -> Site chain, with no
tenant-named Location anywhere in it -- tenancy is expressed via each
Location's separate `tenant` field, not via parentage. The original
`Location.objects.get(name=site_name, parent__name=customer_ns_name)`
lookup could never match a Location this codebase actually creates, so
this now looks up by name alone, matching the name-only Location lookup
convention already used elsewhere (onboarding_mcp's set_site existing-site
path, upload_app.py's `_find_live_firewall_sg()`). Callers are responsible
for the site's Location already existing -- see onboard_site_job.py and
upload_app.py's own docstrings for how each onboarding surface satisfies
that.
"""
import ipaddress

from nautobot.dcim.models import Location
from nautobot.extras.models import Status
from nautobot.ipam.models import Namespace, Prefix


class ShadowIPValidationError(Exception):
    """Raised when a real/shadow CIDR pair fails validation before onboarding a site."""


def onboard_site(
    customer_ns_name,
    site_name,
    real_cidr,
    shadow_cidr,
    fortigate_vdom=None,
    fortigate_vip_name=None,
    fortigate_tunnel_name=None,
):
    """Call this once per site as part of the existing onboarding flow, before device import.

    fortigate_vdom/fortigate_vip_name/fortigate_tunnel_name are optional so
    existing callers that only care about the real/shadow prefix pair keep
    working unchanged; pass them (VIP Management architecture doc §5) to
    also record what ValidateVIPCoverage needs to reconcile this site
    against the live FortiGate VIP object. fortigate_vdom and
    fortigate_tunnel_name are stored on the customer Namespace (one VDOM/
    tunnel per customer, this codebase's existing convention -- see
    fortigate_vdom on custom_fields.py); fortigate_vip_name is stored on
    the shadow Prefix, since a customer can have multiple sites/VIPs inside
    one VDOM.
    """
    real_net = ipaddress.ip_network(real_cidr, strict=False)
    shadow_net = ipaddress.ip_network(shadow_cidr, strict=False)
    if real_net.prefixlen != shadow_net.prefixlen:
        raise ShadowIPValidationError(
            f"real_cidr {real_cidr} and shadow_cidr {shadow_cidr} have different "
            f"prefix lengths ({real_net.prefixlen} vs {shadow_net.prefixlen}) -- "
            f"the shadow prefix must be the same length as the real prefix."
        )

    global_ns = Namespace.objects.get(name="Global")
    # LIVE-VERIFIED on the lab server: PrefixQuerySet on this Nautobot version
    # has no net_overlap() at all (AttributeError, confirmed by introspecting
    # dir() on a live queryset) -- that method name was carried over from the
    # architecture doc's spec code, never actually available. Two valid CIDR
    # blocks can only ever be disjoint, equal, or one strictly containing the
    # other, so "any overlap" is fully covered by combining the two real
    # methods that do exist: net_contains_or_equals (an existing Global
    # prefix is a superset of or equal to shadow_cidr) and
    # net_contained_or_equal (an existing Global prefix is a subset of or
    # equal to shadow_cidr).
    existing_shadow = Prefix.objects.filter(namespace=global_ns)
    if (
        existing_shadow.net_contains_or_equals(shadow_cidr).exists()
        or existing_shadow.net_contained_or_equal(shadow_cidr).exists()
    ):
        raise ShadowIPValidationError(
            f"shadow_cidr {shadow_cidr} overlaps an existing shadow Prefix in the "
            f"Global namespace -- shadow blocks must be unique across every customer."
        )

    customer_ns = Namespace.objects.get(name=customer_ns_name)
    location = Location.objects.filter(name=site_name).first()
    if location is None:
        raise ShadowIPValidationError(
            f"No Location named '{site_name}' exists yet -- create the site's Location "
            f"first (e.g. via the onboarding wizard's Region/Country/State/City/Site "
            f"step) before onboarding its shadow IP prefix pair."
        )

    # LIVE-VERIFIED on the lab server: ipam_prefix.status_id is a NOT NULL
    # column on this Nautobot version -- get_or_create() without a status in
    # defaults raised IntegrityError on the actual INSERT (confirmed via the
    # worker's traceback), not caught by anything upstream. No status is
    # implied/defaulted by Nautobot itself; every Prefix needs one explicitly.
    active_status = Status.objects.get(name="Active")
    shadow_prefix, _ = Prefix.objects.get_or_create(
        prefix=shadow_cidr, namespace=global_ns,
        defaults={"status": active_status},
    )
    real_prefix, _ = Prefix.objects.get_or_create(
        prefix=real_cidr, namespace=customer_ns, location=location,
        defaults={"status": active_status},
    )
    real_prefix.custom_field_data["nat_shadow_prefix"] = str(shadow_prefix.id)
    real_prefix.save()

    if fortigate_vip_name:
        shadow_prefix.custom_field_data["fortigate_vip_name"] = fortigate_vip_name
        shadow_prefix.save()
    if fortigate_vdom:
        customer_ns.custom_field_data["fortigate_vdom"] = fortigate_vdom
    if fortigate_tunnel_name:
        customer_ns.custom_field_data["fortigate_tunnel_name"] = fortigate_tunnel_name
    if fortigate_vdom or fortigate_tunnel_name:
        customer_ns.save()

    return real_prefix, shadow_prefix
