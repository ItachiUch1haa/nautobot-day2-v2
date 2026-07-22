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
import argparse
from flask import Flask, request, jsonify

BROKER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BROKER_DIR)
sys.path.insert(0, os.path.dirname(BROKER_DIR))
sys.path.insert(0, os.path.join(os.path.dirname(BROKER_DIR), "onboarding"))

from core import get_device_context, run_diagnostic_command

app = Flask(__name__)


@app.route("/device/<device_name>", methods=["GET"])
def device_info(device_name):
    """Read-only device metadata lookup — no credential fetch, no dispatch."""
    try:
        ctx = get_device_context(device_name)
        return jsonify(ctx)
    except Exception as e:
        return jsonify({"error": str(e)}), 404


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

    try:
        output = run_diagnostic_command(device_name, command)
        return jsonify({"device": device_name, "command": command, "output": output})
    except Exception as e:
        return jsonify({"device": device_name, "command": command, "error": str(e)}), 500


@app.route("/health")
def health():
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
