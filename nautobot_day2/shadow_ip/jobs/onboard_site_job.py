"""
REST-triggerable wrapper around site_onboarding.onboard_site(). Exists
because onboarding_mcp (and, in principle, the wizard) run as standalone
processes with no Django ORM access -- exactly like the wizard triggers
SyncNetworkData over REST (POST extras/jobs/{id}/run) instead of importing
Job code directly, onboarding_mcp's set_site tool triggers this Job the
same way rather than calling onboard_site() in-process.

BUG FOUND AND FIXED via live lab-server testing: this file never called
register_jobs(OnboardSite) -- every sibling job in this package does, at
the bottom of its module. Nautobot's App job-discovery only refreshes
jobs that went through register_jobs(); the class imported fine (Python
had no complaint) but Nautobot's `Job` table never learned it existed, so
neither onboarding surface could ever actually trigger it, despite both
being built and wired to call it by name over REST. Pre-existing since
the very first shadow_ip commit -- not introduced by later changes to
this file, just never caught until the first real end-to-end test.
"""
from nautobot.extras.jobs import Job, StringVar
from nautobot.apps.jobs import register_jobs

from ..site_onboarding import ShadowIPValidationError, onboard_site

_VIP_FIELDS_HELP = (
    " Optional -- set these to also register the site for ValidateVIPCoverage "
    "reconciliation against the live FortiGate VIP object (VIP Management "
    "architecture doc §5/§6.5); leave blank if this site's VIP isn't being "
    "tracked yet."
)


class OnboardSite(Job):
    """Create a site's real+shadow Prefix pair and link them, before any device import."""

    class Meta:
        """Declares the job's display name and description shown in the Nautobot job list."""

        name = "Shadow IP: Onboard Site (real+shadow prefix pair)"
        description = (
            "Creates a site's real Prefix (in its customer namespace) and shadow "
            "Prefix (in Global), and links them via nat_shadow_prefix. Run once per "
            "site, before any device/IP is created for it."
        )
        has_sensitive_variables = False

    customer_ns_name = StringVar(
        label="Customer namespace",
        description="Name of the tenant's IP namespace (created during tenant onboarding)",
    )
    site_name = StringVar(
        label="Site name",
        description="Name of the site Location, child of the customer's top-level Location",
    )
    real_cidr = StringVar(
        label="Real CIDR",
        description="The site's real subnet, e.g. 10.0.1.0/24",
    )
    shadow_cidr = StringVar(
        label="Shadow CIDR",
        description="The paired shadow subnet in 100.64.0.0/10, same prefix length as real_cidr",
    )
    fortigate_vdom = StringVar(
        label="FortiGate VDOM",
        description="e.g. custA -- the VDOM this customer's traffic lives in." + _VIP_FIELDS_HELP,
        required=False,
        default="",
    )
    fortigate_vip_name = StringVar(
        label="FortiGate VIP name",
        description="e.g. VIP-CUST-A -- the literal static-nat VIP object name on the firewall."
        + _VIP_FIELDS_HELP,
        required=False,
        default="",
    )
    fortigate_tunnel_name = StringVar(
        label="FortiGate tunnel name",
        description="e.g. Tunnel-CUSTA -- for cross-referencing tunnel health separately."
        + _VIP_FIELDS_HELP,
        required=False,
        default="",
    )

    def run(
        self,
        customer_ns_name,
        site_name,
        real_cidr,
        shadow_cidr,
        fortigate_vdom="",
        fortigate_vip_name="",
        fortigate_tunnel_name="",
    ):
        """Validate and create the real/shadow Prefix pair, logging the result."""
        try:
            real_prefix, shadow_prefix = onboard_site(
                customer_ns_name,
                site_name,
                real_cidr,
                shadow_cidr,
                fortigate_vdom=fortigate_vdom or None,
                fortigate_vip_name=fortigate_vip_name or None,
                fortigate_tunnel_name=fortigate_tunnel_name or None,
            )
        except ShadowIPValidationError as e:
            self.logger.error(str(e))
            raise
        self.logger.info(
            f"Onboarded site '{site_name}' ({customer_ns_name}): "
            f"real prefix {real_prefix.prefix} <-> shadow prefix {shadow_prefix.prefix}"
        )
        return {
            "real_prefix_id": str(real_prefix.id),
            "real_prefix": str(real_prefix.prefix),
            "shadow_prefix_id": str(shadow_prefix.id),
            "shadow_prefix": str(shadow_prefix.prefix),
        }


register_jobs(OnboardSite)
