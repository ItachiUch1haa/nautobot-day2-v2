"""
Scheduled Job (VIP Management architecture doc §6.3) that closes the gap
ReconcileDeviceIPs leaves open: ReconcileDeviceIPs only updates devices
Nautobot already knows about, keyed by MAC. This job compares each
customer VDOM's live DHCP lease table against every known Interface MAC
address and flags anything unrecognized -- a new AP, a replacement switch,
any device joining the network for the first time -- rather than silently
doing nothing until a human notices it isn't manageable.

Deliberately conservative, per the architecture doc: it does NOT
auto-create a Device from a MAC address alone (a MAC tells you nothing
about device role, name, or type). This only produces a reviewable log
flag; promoting a flag to a real Device record is a human decision (or a
separate, more opinionated job with vendor-OUI lookup / LLDP data) -- see
architecture doc §8.6, an explicitly open decision, not resolved here.
"""
from nautobot.apps.jobs import Job, register_jobs
from nautobot.dcim.models import Interface
from nautobot.ipam.models import Namespace, Prefix

from ..integrations import fortigate_client


class DiscoverNewDevices(Job):
    """Flag unrecognized MAC addresses seen in live FortiGate DHCP leases, per customer VDOM."""

    class Meta:
        """Declares the job's display name and description shown in the Nautobot job list."""

        name = "Discover new devices from live DHCP leases"
        description = (
            "Flags unrecognized MAC addresses seen in live FortiGate DHCP leases; "
            "does not auto-create full device records without review."
        )
        has_sensitive_variables = False

    def run(self):
        """For every customer namespace with a configured VDOM, flag any unrecognized MAC."""
        known_macs = {
            str(mac).lower()
            for mac in Interface.objects.exclude(mac_address__isnull=True).values_list(
                "mac_address", flat=True
            )
        }

        for customer_ns in Namespace.objects.exclude(name="Global"):
            vdom = customer_ns.custom_field_data.get("fortigate_vdom")
            if not vdom:
                continue
            try:
                live_leases = fortigate_client.get_dhcp_leases(vdom=vdom)
            except Exception as e:
                self.logger.error(f"{customer_ns}: could not fetch DHCP leases — {e}")
                continue

            for mac, real_ip in live_leases.items():
                if mac.lower() in known_macs:
                    continue  # already tracked -- ReconcileDeviceIPs handles any IP change for this one

                real_prefix = Prefix.objects.filter(namespace=customer_ns).net_contains(real_ip).first()
                if not real_prefix:
                    self.logger.warning(
                        f"{customer_ns}: unrecognized MAC {mac} at {real_ip} — no matching "
                        f"prefix, cannot even place it at a site"
                    )
                    continue

                self.logger.warning(
                    f"{customer_ns}: unrecognized MAC {mac} leased {real_ip} at site "
                    f"{real_prefix.location} — not yet in Nautobot. Flagging for review, "
                    f"not auto-onboarding."
                )
                # Deliberately does NOT auto-create a Device here -- see module docstring.


register_jobs(DiscoverNewDevices)
