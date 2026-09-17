"""Example custom MCP server exposing YOUR internal APIs as agent tools.

Replace the two example tools below with calls into your real backend
(REST calls via httpx, direct DB queries, etc). Anything you decorate with
@mcp.tool() is automatically discovered by MCPManager on startup -- no
other code needs to change.

Run standalone for testing: python -m app.mcp_servers.internal_api_server
"""
import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("internal_api")

INTERNAL_API_BASE = "https://api.yourproduct.example.com"


@mcp.tool()
async def get_user_profile(user_id: str) -> str:
    """Look up a user's profile by internal user ID.

    Args:
        user_id: The internal user ID to look up.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{INTERNAL_API_BASE}/users/{user_id}")
        resp.raise_for_status()
        return resp.text


@mcp.tool()
async def create_support_ticket(subject: str, description: str, user_id: str) -> str:
    """Create a support ticket in the internal ticketing system.

    Args:
        subject: Short ticket subject line.
        description: Full description of the issue.
        user_id: The internal user ID the ticket is filed for.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{INTERNAL_API_BASE}/tickets",
            json={"subject": subject, "description": description, "user_id": user_id},
        )
        resp.raise_for_status()
        return resp.text


if __name__ == "__main__":
    mcp.run(transport="stdio")
