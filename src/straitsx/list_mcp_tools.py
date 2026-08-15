"""Run this once you have network access to card.straitsx.ai to see exactly
what tools the sandbox MCP server exposes, so mcp_client.py's find_tool()
calls can be replaced with exact names if needed.

Usage:
    python -m src.straitsx.list_mcp_tools
    STRAITSX_MCP_ENV=production python -m src.straitsx.list_mcp_tools
"""
from __future__ import annotations

import asyncio
import json

from src.straitsx.mcp_client import StraitsXCardMCPClient


async def main() -> None:
    client = StraitsXCardMCPClient()
    print(f"Connecting to {client.url} ...")
    tools = await client.list_tools()
    if not tools:
        print("No tools returned.")
        return
    for t in tools:
        print(f"\n- {t['name']}")
        if t.get("description"):
            print(f"    {t['description']}")
        if t.get("input_schema"):
            print(f"    input schema: {json.dumps(t['input_schema'], indent=2)}")


if __name__ == "__main__":
    asyncio.run(main())
