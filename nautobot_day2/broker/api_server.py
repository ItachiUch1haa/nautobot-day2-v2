"""
broker/api_server.py
Agent Broker — REST API wrapper.
Thin HTTP layer over broker/core.py — all actual logic (Nautobot lookup,
OpenBao credential fetch, vendor resolution, Netmiko dispatch) lives in
core.py so the REST and MCP interfaces share one implementation.
Serves on port 8082.
Usage:
    python3 api_server.py
    python3 api_server.py --port 8082 --debug
"""
import sys
import os
import time
import argparse
from flask import Flask, request, jsonify
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

BROKER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BROKER_DIR)
sys.path.insert(0, os.path.dirname(BROKER_DIR))
sys.path.insert(0, os.path.join(os.path.dirname(BROKER_DIR), "onboarding"))
from core import get_device_context, run_diagnostic_command, run_diagnostic_commands

app = Flask(__name__)

# --- Prometheus instrumentation --------------------------------------------
# Deliberately wraps the REST boundary, not core.py -- core.py's dispatch
# logic (Nornir/Netmiko/FortiAP-redirect/API-vendor branching) stays
# untouched. "endpoint" + "outcome" are low-cardinality; "device" is
# bounded by the size of this lab's device inventory.
BROKER_REQUESTS = Counter(
    "broker_command_requests_total",
    "Agent Broker REST requests by endpoint/device/outcome",
    ["endpoint", "device", "outcome"],
)
BROKER_DURATION = Histogram(
    "broker_command_duration_seconds",
    "Agent Broker REST request duration by endpoint",
    ["endpoint"],
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120),
)


@app.route("/metrics")
def metrics():
    """Expose Prometheus metrics for the broker's REST endpoints."""
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


@app.route("/device/<device_name>", methods=["GET"])
def device_info(device_name):
    """Read-only device metadata lookup — no credential fetch, no dispatch."""
    start = time.time()
    try:
        ctx = get_device_context(device_name)
        BROKER_REQUESTS.labels(endpoint="device_info", device=device_name, outcome="ok").inc()
        return jsonify(ctx)
    except Exception as e:
        BROKER_REQUESTS.labels(endpoint="device_info", device=device_name, outcome="error").inc()
        return jsonify({"error": str(e)}), 404
    finally:
        BROKER_DURATION.labels(endpoint="device_info").observe(time.time() - start)


@app.route("/diagnose", methods=["POST"])
def diagnose():
    """
    Body: {"device": "<device_name>", "command": "<command string>"}
    Runs the command against the device and returns raw output.
    No command allowlist enforced (explicit project decision).
    """
    data = request.get_json(silent=True) or {}
    device_name = data.get("device")
    command = data.get("command")
    if not device_name or not command:
        return jsonify({"error": "MISSING_FIELDS: both 'device' and 'command' are required"}), 400
    start = time.time()
    try:
        output = run_diagnostic_command(device_name, command)
        BROKER_REQUESTS.labels(endpoint="diagnose", device=device_name, outcome="ok").inc()
        return jsonify({"device": device_name, "command": command, "output": output})
    except Exception as e:
        BROKER_REQUESTS.labels(endpoint="diagnose", device=device_name, outcome="error").inc()
        return jsonify({"device": device_name, "command": command, "error": str(e)}), 500
    finally:
        BROKER_DURATION.labels(endpoint="diagnose").observe(time.time() - start)


@app.route("/diagnose_batch", methods=["POST"])
def diagnose_batch():
    """
    Body: {"device": "<device_name>", "commands": ["<command 1>", ...]}
    Runs every command against the device over ONE connection (SSH) or
    the shared HTTP session (API-managed devices), instead of one
    connection per command. A failure on one command does not abort the
    rest of the batch. No command allowlist enforced (explicit project
    decision, same as /diagnose).
    """
    data = request.get_json(silent=True) or {}
    device_name = data.get("device")
    commands = data.get("commands")
    if not device_name or not isinstance(commands, list) or not commands:
        return jsonify({"error": "MISSING_FIELDS: 'device' and a non-empty 'commands' list are required"}), 400
    start = time.time()
    try:
        results = run_diagnostic_commands(device_name, commands)
        outcome = "error" if any(r["error"] for r in results) else "ok"
        BROKER_REQUESTS.labels(endpoint="diagnose_batch", device=device_name, outcome=outcome).inc()
        return jsonify({"device": device_name, "results": results})
    except Exception as e:
        BROKER_REQUESTS.labels(endpoint="diagnose_batch", device=device_name, outcome="error").inc()
        return jsonify({"device": device_name, "error": str(e)}), 500
    finally:
        BROKER_DURATION.labels(endpoint="diagnose_batch").observe(time.time() - start)


@app.route("/health")
def health():
    """Report basic liveness status for the agent broker service."""
    return jsonify({"status": "ok", "service": "agent-broker"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nautobot Day2 Agent Broker — REST API")
    parser.add_argument("--port", type=int, default=8082)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    print(f"\n  Agent Broker — REST API")
    print(f"  URL: http://{args.host}:{args.port}\n")
    app.run(host=args.host, port=args.port, debug=args.debug)
