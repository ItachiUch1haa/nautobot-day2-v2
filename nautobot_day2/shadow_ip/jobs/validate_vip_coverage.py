"""
Read-only reconciliation Job (VIP Management architecture doc §6.5) --
queries each customer VDOM's actual static-NAT VIP object via the FortiOS
REST API and compares it against Nautobot's recorded shadow/real Prefix
pair. This is "maintaining the VIP": it never writes to the firewall, only
reads and reports -- the firewall's running config stays the sole
authority for what's actually live (architecture doc §1).

Run daily. Architecture doc §8.1 flags routing a mismatch here to a
ticket/alert channel as an open decision (a VIP mismatch likely means a
customer is currently unreachable) -- not resolved here, log-only for now.
"""
from nautobot.apps.jobs import Job, register_jobs
from nautobot.ipam.models import Prefix

from ..integrations import fortigate_client
from ..shadow_math import range_from_prefix, range_size


class ValidateVIPCoverage(Job):
    """Compare each recorded shadow/real Prefix pair against its live FortiGate VIP object."""

    class Meta:
        """Declares the job's display name and description shown in the Nautobot job list."""

        name = "Validate FortiGate VIP objects match Nautobot records"
        description = "Read-only reconciliation. Never writes to the firewall."
        has_sensitive_variables = False

    def run(self):
        """For every shadow Prefix with VIP fields set, diff it against the live firewall VIP."""
        for shadow_prefix in Prefix.objects.filter(namespace__name="Global"):
            vip_name = shadow_prefix.custom_field_data.get("fortigate_vip_name")
            real_prefix = Prefix.objects.exclude(namespace__name="Global").filter(
                custom_field_data__nat_shadow_prefix=str(shadow_prefix.id)
            ).first()
            if not vip_name or not real_prefix:
                continue  # not a VIP-tracked shadow prefix, or no real prefix linked back to it yet
            vdom = real_prefix.namespace.custom_field_data.get("fortigate_vdom")
            if not vdom:
                self.logger.error(
                    f"{vip_name}: real prefix {real_prefix} has no fortigate_vdom on its "
                    f"namespace {real_prefix.namespace} — cannot query the firewall"
                )
                continue

            try:
                live_vip = fortigate_client.get_vip(vdom=vdom, name=vip_name)
            except Exception as e:
                self.logger.error(f"{vip_name} in {vdom}: could not fetch VIP — {e}")
                continue

            if live_vip is None:
                self.logger.error(
                    f"{vip_name} in {vdom}: VIP not found on firewall — Nautobot has a "
                    f"record with no matching config"
                )
                continue

            expected_extip = range_from_prefix(shadow_prefix.prefix)
            expected_mappedip = range_from_prefix(real_prefix.prefix)

            if live_vip["extip"] != expected_extip:
                self.logger.error(
                    f"{vip_name}: extip mismatch — firewall has {live_vip['extip']}, "
                    f"Nautobot expects {expected_extip}"
                )
            if live_vip["mappedip"] != expected_mappedip:
                self.logger.error(
                    f"{vip_name}: mappedip mismatch — firewall has {live_vip['mappedip']}, "
                    f"Nautobot expects {expected_mappedip}"
                )
            if live_vip.get("type") != "static-nat":
                self.logger.error(
                    f"{vip_name}: expected type static-nat, firewall has {live_vip.get('type')}"
                )

            # Range-size equality check -- required for shadow_math's offset formula to be
            # valid at all (architecture doc §4); a config mistake here silently produces
            # wrong shadow IPs rather than an obvious failure, so check it explicitly.
            if live_vip["extip"] and live_vip["mappedip"]:
                extip_size = range_size(live_vip["extip"])
                mappedip_size = range_size(live_vip["mappedip"])
                if extip_size != mappedip_size:
                    self.logger.error(
                        f"{vip_name}: extip/mappedip range sizes differ "
                        f"({extip_size} vs {mappedip_size}) — offset math will produce "
                        f"wrong shadow IPs"
                    )


register_jobs(ValidateVIPCoverage)
