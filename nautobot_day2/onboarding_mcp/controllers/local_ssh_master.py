"""
local_ssh_master controller adapter -- a local wireless controller
(Aruba mobility controller, Ruckus SmartZone, etc.) reachable directly
over SSH. Reuses the exact same Nornir/nornir_netmiko dispatch pattern as
broker/core.py's run_diagnostic_commands() (build the Nornir object
directly, no InitNornir(), one ConnectHandler login reused for the whole
call) rather than a new SSH implementation.

PENDING LIVE VERIFICATION (per master doc §7 item 1 / brief Phase 4):
the per-platform discovery commands and output parsers below are
Aruba-first (matching architecture doc §5's own example, "Aruba `show ap
database`") and have not been run against real hardware in this build
pass. Verify field parsing against an actual controller before trusting
discover_aps()'s output; add a real Ruckus parser only once one has been
seen and verified too.
"""
from controllers.base import APControllerClient, ControllerAuthError

# vendor/platform -> (discovery command, parser function name below).
# Extend this as more controller platforms are actually verified live.
_DISCOVERY_COMMANDS = {
    "aruba_os_cx_mobility_controller": "show ap database",
}


def _parse_aruba_ap_database(raw_output):
    """
    Parse Aruba `show ap database` text output into the common candidate
    shape. PENDING LIVE VERIFICATION -- column layout assumed from public
    Aruba documentation, not yet confirmed against a real controller's
    actual output.
    """
    candidates = []
    for line in raw_output.splitlines():
        line = line.strip()
        if not line or line.lower().startswith(("name", "---", "total")):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        name, ip_addr, mac, model = parts[0], parts[1], parts[2], parts[3]
        candidates.append({
            "name": name,
            "model": model,
            "mac": mac,
            "current_ip": ip_addr if ip_addr not in ("-", "0.0.0.0") else None,
            "site_label": None,
            "raw": line,
        })
    return candidates


_PARSERS = {
    "aruba_os_cx_mobility_controller": _parse_aruba_ap_database,
}


class LocalSSHMasterClient(APControllerClient):
    """AP discovery against a local wireless controller reachable over SSH."""

    def __init__(self, mgmt_ip, username, password_or_key, platform="aruba_os_cx_mobility_controller"):
        self.mgmt_ip = mgmt_ip
        self.username = username
        self.password_or_key = password_or_key
        self.platform = platform

    def required_credential_fields(self):
        """local_ssh_master collects mgmt_ip, username, password_or_key (architecture doc §5)."""
        return ["mgmt_ip", "username", "password_or_key"]

    def _run(self, command):
        from nornir.core import Nornir
        from nornir.core.inventory import Host, Inventory
        from nornir.core.plugins.connections import ConnectionPluginRegister
        from nornir.plugins.runners import ThreadedRunner
        from nornir_netmiko.tasks import netmiko_send_command

        ConnectionPluginRegister.auto_register()
        host = Host(
            name="controller",
            hostname=self.mgmt_ip,
            username=self.username,
            password=self.password_or_key,
            platform=self.platform,
        )
        nr = Nornir(inventory=Inventory(hosts={"controller": host}), runner=ThreadedRunner(num_workers=1))
        try:
            result = nr.run(
                task=netmiko_send_command,
                command_string=command,
                use_timing=True,
                delay_factor=2,
                strip_prompt=True,
                strip_command=True,
            )
            host_result = result["controller"]
            if host_result[0].failed:
                raise ControllerAuthError(f"NORNIR_DISPATCH_FAILED: {host_result[0].exception}")
            return host_result[0].result
        finally:
            nr.close_connections()

    def test_connection(self):
        """Confirm the controller is reachable and the credentials authenticate, by running its discovery command once."""
        try:
            self._run(_DISCOVERY_COMMANDS.get(self.platform, "show version"))
            return True
        except Exception as e:
            raise ControllerAuthError(f"local_ssh_master test_connection failed for {self.mgmt_ip}: {e}")

    def discover_aps(self, filters):
        """Run this platform's discovery command and parse the output into the common candidate shape."""
        command = _DISCOVERY_COMMANDS.get(self.platform)
        if not command:
            raise NotImplementedError(f"No discovery command configured for platform '{self.platform}'")
        raw_output = self._run(command)
        parser = _PARSERS.get(self.platform)
        candidates = parser(raw_output) if parser else []

        name_contains = (filters or {}).get("name_contains")
        if name_contains:
            candidates = [c for c in candidates if name_contains.lower() in c["name"].lower()]
        return candidates
