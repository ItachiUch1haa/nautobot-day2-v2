"""
broker/mcp_server.py
Agent Broker — MCP server wrapper.
Thin MCP tool layer over broker/core.py — same underlying logic as
api_server.py's REST routes, so both interfaces share one implementation
and can't drift apart.
Serves via streamable-http transport on port 8090.
Usage:
    python3 mcp_server.py
"""
import sys
import os

BROKER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BROKER_DIR)
sys.path.insert(0, os.path.dirname(BROKER_DIR))
sys.path.insert(0, os.path.join(os.path.dirname(BROKER_DIR), "onboarding"))

from core import get_device_context, run_diagnostic_command
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("nautobot-day2-agent-broker", host="0.0.0.0", port=8090)


@mcp.tool()
def get_device_info(device_name: str) -> dict:
    """
    Look up a device's metadata in Nautobot: tenant, IP, platform, role,
    secrets group. Read-only — does not fetch credentials or connect to
    the device.
    """
    try:
        return get_device_context(device_name)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def run_command(device_name: str, command: str) -> dict:
    """
    Run a diagnostic command against a real network device (switch,
    firewall, or access point) and return its raw output. Fetches the
    device's credential from OpenBao and dispatches over SSH (or the
    vendor's API for cloud-managed devices, once supported). No command
    allowlist is enforced — any command string will be attempted as-is.
    """
    try:
        output = run_diagnostic_command(device_name, command)
        return {"device": device_name, "command": command, "output": output}
    except Exception as e:
        return {"device": device_name, "command": command, "error": str(e)}


if __name__ == "__main__":
    print("\n  Agent Broker — MCP Server (streamable-http)")
    print("  URL: http://0.0.0.0:8090/mcp\n")
    mcp.run(transport="streamable-http")
