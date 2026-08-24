"""
Scheduled Job (schedule via Nautobot's Job Scheduling UI, 10-15 min per
customer -- see docs/00-WORKFLOW.md-style ops notes) that catches
DHCP/roaming IP drift: compares each device's currently recorded real IP
against the FortiGate NVA's live DHCP lease table, scoped per VDOM, and
updates the shadow mapping when they diverge. Deprecates the stale shadow
IP rather than deleting it, to keep an audit trail.

Also the mechanism that picks up an IP for a controller-discovered AP that
had none yet at onboarding_mcp deploy time (see
onboarding_mcp/deploy/nautobot_deployer.py) -- no separate polling logic
needed there, this job already owns catching it.
"""
from nautobot.apps.jobs import Job, register_jobs
from nautobot.dcim.models import Device
from nautobot.extras.models import Status
from nautobot.ipam.models import IPAddress, Namespace, Prefix

from ..integrations import fortigate_client
from ..shadow_math import compute_shadow_ip


class ReconcileDeviceIPs(Job):
    """Reconcile each device's real IP against the FortiGate NVA's live DHCP leases, per tenant VDOM."""

    class Meta:
        """Declares the job's display name and description shown in the Nautobot job list."""

        name = "Reconcile device real IPs (DHCP drift)"
        has_sensitive_variables = False

    def run(self):
        """For every customer namespace with a configured VDOM, catch and correct real-IP drift."""
        for customer_ns in Namespace.objects.exclude(name="Global"):
            vdom = customer_ns.custom_field_data.get("fortigate_vdom")
            if not vdom:
                continue
            try:
                live_leases = fortigate_client.get_dhcp_leases(vdom=vdom)
            except Exception as e:
                self.logger.error(f"{customer_ns}: could not fetch DHCP leases — {e}")
                continue

            devices = Device.objects.filter(
                location__namespace=customer_ns
            ).select_related("primary_ip4")
            for device in devices:
                iface = device.interfaces.first()
                if not iface or not iface.mac_address:
                    continue
                live_real_ip = live_leases.get(str(iface.mac_address).lower())
                if not live_real_ip:
                    continue

                current_shadow = device.primary_ip4
                current_real_ip = (
                    current_shadow.custom_field_data.get("real_ip") if current_shadow else None
                )

                if current_real_ip == live_real_ip:
                    continue  # no drift

                self.logger.info(f"{device.name}: real IP changed {current_real_ip} -> {live_real_ip}")

                real_prefix = Prefix.objects.filter(
                    namespace=customer_ns
                ).net_contains(live_real_ip).first()
                if not real_prefix:
                    self.logger.error(f"{live_real_ip} matches no known prefix in {customer_ns} — skipping")
                    continue

                shadow_prefix_id = real_prefix.custom_field_data.get("nat_shadow_prefix")
                if not shadow_prefix_id:
                    self.logger.error(f"{real_prefix} has no nat_shadow_prefix mapped — skipping")
                    continue
                shadow_prefix = Prefix.objects.get(id=shadow_prefix_id)
                new_shadow_str = compute_shadow_ip(live_real_ip, real_prefix, shadow_prefix)

                new_shadow, _ = IPAddress.objects.get_or_create(
                    address=f"{new_shadow_str}/{shadow_prefix.prefix_length}",
                    namespace=Namespace.objects.get(name="Global"),
                    defaults={"custom_field_data": {"real_ip": live_real_ip}},
                )
                device.primary_ip4 = new_shadow
                device.save()

                if current_shadow:
                    current_shadow.status = Status.objects.get(name="Deprecated")
                    current_shadow.save()


register_jobs(ReconcileDeviceIPs)
