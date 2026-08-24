"""
onboarding_mcp/server.py
Conversational onboarding — MCP server wrapper.
Thin MCP tool layer over tools_schema.py's 11 tools (architecture doc §4)
-- same streamable-http FastMCP setup as broker/mcp_server.py, so the same
initialize -> session-id -> tools/list handshake works against this
server on port 8091.
Usage:
    python3 server.py
"""
import functools
import sys
import os

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SERVER_DIR)

import tools_schema as ts  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("nautobot-day2-onboarding-mcp", host="0.0.0.0", port=8091)


def _wrap(fn):
    """
    Catch ts.ToolError (and anything else) and return {"error": ...}
    instead of raising through MCP — same failure-shape convention as
    broker/mcp_server.py. Uses functools.wraps (not just copying
    __name__/__doc__) so inspect.signature() -- which FastMCP relies on
    to build each tool's JSON schema -- follows __wrapped__ back to fn's
    real parameter names/types instead of seeing a bare (*args, **kwargs).
    """
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ts.ToolError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"UNEXPECTED_ERROR: {e}"}
    return wrapped


mcp.tool()(_wrap(ts.start_onboarding))
mcp.tool()(_wrap(ts.set_tenant))
mcp.tool()(_wrap(ts.set_site))
mcp.tool()(_wrap(ts.add_static_device))
mcp.tool()(_wrap(ts.set_ap_controller))
mcp.tool()(_wrap(ts.scan_ap_controller))
mcp.tool()(_wrap(ts.select_discovered_aps))
mcp.tool()(_wrap(ts.review_pending_batch))
mcp.tool()(_wrap(ts.remove_pending_device))
mcp.tool()(_wrap(ts.deploy_site))
mcp.tool()(_wrap(ts.get_session_status))


if __name__ == "__main__":
    print("\n  Onboarding MCP — Conversational Onboarding Server (streamable-http)")
    print("  URL: http://0.0.0.0:8091/mcp\n")
    print("  NOTE: no authentication on this service yet (architecture doc §11,")
    print("  first bullet) -- higher privilege than the Agent Broker (writes")
    print("  credentials + creates infra). See docs/06-GAPS-AND-RECOMMENDATIONS.md.\n")
    mcp.run(transport="streamable-http")
