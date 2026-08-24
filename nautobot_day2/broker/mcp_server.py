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
import time
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

BROKER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BROKER_DIR)
sys.path.insert(0, os.path.dirname(BROKER_DIR))
sys.path.insert(0, os.path.join(os.path.dirname(BROKER_DIR), "onboarding"))
from core import get_device_context, run_diagnostic_command, run_diagnostic_commands
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("nautobot-day2-agent-broker", host="0.0.0.0", port=8090)

# --- Prometheus instrumentation --------------------------------------------
# Same metric names as api_server.py's, distinguished by the "job" label
# Prometheus itself attaches per scrape target — lets the Grafana dashboard
# sum across both interfaces or split by job as needed.
BROKER_REQUESTS = Counter(
    "broker_command_requests_total",
    "Agent Broker MCP tool calls by endpoint/device/outcome",
    ["endpoint", "device", "outcome"],
)
BROKER_DURATION = Histogram(
    "broker_command_duration_seconds",
    "Agent Broker MCP tool call duration by endpoint",
    ["endpoint"],
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120),
)


@mcp.custom_route("/metrics", methods=["GET"])
async def metrics(request: Request) -> PlainTextResponse:
    """Expose Prometheus metrics for the broker's MCP tool calls."""
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@mcp.tool()
def get_device_info(device_name: str) -> dict:
    """
    Look up a device's metadata in Nautobot: tenant, IP, platform, role,
    secrets group. Read-only — does not fetch credentials or connect to
    the device.
    """
    start = time.time()
    try:
        result = get_device_context(device_name)
        BROKER_REQUESTS.labels(endpoint="get_device_info", device=device_name, outcome="ok").inc()
        return result
    except Exception as e:
        BROKER_REQUESTS.labels(endpoint="get_device_info", device=device_name, outcome="error").inc()
        return {"error": str(e)}
    finally:
        BROKER_DURATION.labels(endpoint="get_device_info").observe(time.time() - start)


@mcp.tool()
def run_command(device_name: str, command: str) -> dict:
    """
    Run a diagnostic command against a real network device (switch,
    firewall, or access point) and return its raw output. Fetches the
    device's credential from OpenBao and dispatches over SSH (or the
    vendor's API for cloud-managed devices, once supported). No command
    allowlist is enforced — any command string will be attempted as-is.
    """
    start = time.time()
    try:
        output = run_diagnostic_command(device_name, command)
        BROKER_REQUESTS.labels(endpoint="run_command", device=device_name, outcome="ok").inc()
        return {"device": device_name, "command": command, "output": output}
    except Exception as e:
        BROKER_REQUESTS.labels(endpoint="run_command", device=device_name, outcome="error").inc()
        return {"device": device_name, "command": command, "error": str(e)}
    finally:
        BROKER_DURATION.labels(endpoint="run_command").observe(time.time() - start)


@mcp.tool()
def run_commands_batch(device_name: str, commands: list) -> dict:
    """
    Run a LIST of diagnostic commands against a real network device over
    ONE connection (SSH) or the shared HTTP session (API-managed
    devices), instead of one connection per command. Use this instead of
    repeated run_command() calls when asking a device several things in
    a row -- it avoids paying a full SSH handshake per question. A
    failure on one command does not abort the rest of the batch. No
    command allowlist is enforced — any command string will be attempted
    as-is.
    """
    start = time.time()
    try:
        results = run_diagnostic_commands(device_name, commands)
        outcome = "error" if any(r["error"] for r in results) else "ok"
        BROKER_REQUESTS.labels(endpoint="run_commands_batch", device=device_name, outcome=outcome).inc()
        return {"device": device_name, "results": results}
    except Exception as e:
        BROKER_REQUESTS.labels(endpoint="run_commands_batch", device=device_name, outcome="error").inc()
        return {"device": device_name, "error": str(e)}
    finally:
        BROKER_DURATION.labels(endpoint="run_commands_batch").observe(time.time() - start)


if __name__ == "__main__":
    print("\n  Agent Broker — MCP Server (streamable-http)")
    print("  URL: http://0.0.0.0:8090/mcp\n")
    mcp.run(transport="streamable-http")
