"""Swift-as-an-MCP-server (Step 6).

External clients (Claude Desktop, Cursor, other agents) reach Swift's
article pipeline through this package. It is deliberately kept
separate from :mod:`backend.agents.mcp_clients` (which makes Swift an
MCP *client* of other servers like ``mcp-server-fetch`` and Serper);
the two concerns share a name but no code.
"""

from backend.mcp.server import (
    MCPBearerAuthMiddleware,
    build_mcp_server,
    get_mcp_server,
)

__all__ = [
    "MCPBearerAuthMiddleware",
    "build_mcp_server",
    "get_mcp_server",
]
