"""Client for StraitsX's card-issuance MCP server.

StraitsX exposes card issuance/redemption as a remote MCP server over SSE:
    sandbox:    https://card.straitsx.ai/sandbox/sse
    production: https://card.straitsx.ai/production/sse

This is the recommended integration path if you don't have a StraitsX
business account: the sandbox MCP server handles StraitsX-side auth for you,
unlike the raw REST API in straitsx/client.py which needs API key/secret
credentials tied to a business account.

Because the exact tool names/schemas are defined server-side (and may
change), this client discovers them at connect time via list_tools()
rather than hardcoding names. Run:

    python -m src.straitsx.list_mcp_tools

to print every tool the server exposes, along with its description and
input schema, before wiring up real calls.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, Optional

from mcp import ClientSession
from mcp.client.sse import sse_client

from src.config import settings


class StraitsXCardMCPClient:
    def __init__(self, url: Optional[str] = None) -> None:
        self.url = url or settings.straitsx_mcp_url

    @asynccontextmanager
    async def _session(self):
        async with sse_client(self.url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    async def list_tools(self) -> list[dict]:
        """Returns every tool the MCP server exposes: name, description, input schema."""
        async with self._session() as session:
            result = await session.list_tools()
            return [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.inputSchema,
                }
                for t in result.tools
            ]

    async def call_tool(self, name: str, arguments: dict) -> Any:
        async with self._session() as session:
            return await session.call_tool(name, arguments)

    async def find_tool(self, *keywords: str) -> Optional[str]:
        """Best-effort match: first tool whose name+description contains all keywords."""
        tools = await self.list_tools()
        for t in tools:
            haystack = f"{t['name']} {t.get('description') or ''}".lower()
            if all(k.lower() in haystack for k in keywords):
                return t["name"]
        return None

    async def get_card_sandbox(
        self, wallet_address: str, cardholder_name: str, amount_sgd: float
    ) -> dict:
        """Calls the sandbox card-issuance tool. This does NOT return a ready-to-use card -- it
        returns a payload containing a `cardapi` URL and x402 payment
        requirements. The caller must then speak x402 directly to that URL
        (see x402_client.pay_and_fetch) to actually get the card back.
        """
        tool_name = "get_card_sandbox"
        try:
            result = await self.call_tool(
                tool_name,
                {
                    "wallet_address": wallet_address,
                    "cardholder_name": cardholder_name,
                    "amount_sgd": amount_sgd,
                },
            )
        except Exception:
            fallback = await self.find_tool("card", "sandbox") or await self.find_tool("get", "card")
            if not fallback:
                raise
            result = await self.call_tool(
                fallback,
                {
                    "wallet_address": wallet_address,
                    "cardholder_name": cardholder_name,
                    "amount_sgd": amount_sgd,
                },
            )
        return _parse_mcp_result(result)

    async def view_card_sandbox(self, card_opaque_id: str, settlement_tx: str, wallet_address: str) -> dict:
        """Returns a fresh one-time iframe URL for a previously issued
        sandbox card. Ownership is verified cryptographically against
        wallet_address, so this must be called with the same wallet that
        paid for the card.
        """
        result = await self.call_tool(
            "view_card_sandbox",
            {
                "card_opaque_id": card_opaque_id,
                "settlement_tx": settlement_tx,
                "wallet_address": wallet_address,
            },
        )
        return _parse_mcp_result(result)

    async def issue_virtual_card(
        self, amount_sgd: float, currency: str = "SGD", merchant: Optional[str] = None
    ) -> dict:
        """Generic fallback for non-sandbox/production tool naming, kept in
        case the production server exposes a simpler single-call flow.
        Prefer get_card_sandbox() for the confirmed sandbox tool.
        """
        tool_name = await self.find_tool("card", "issue") or await self.find_tool("virtual", "card")
        if not tool_name:
            raise RuntimeError(
                "No card-issuance tool found on the MCP server. Run "
                "`python -m src.straitsx.list_mcp_tools` to inspect what's available and "
                "update StraitsXCardMCPClient.issue_virtual_card() with the exact tool name/args."
            )
        arguments: dict = {"amount": amount_sgd, "currency": currency}
        if merchant:
            arguments["merchant"] = merchant
        result = await self.call_tool(tool_name, arguments)
        return _parse_mcp_result(result)

    async def redeem_xsgd(self, amount_sgd: float, tx_hash: str) -> dict:
        tool_name = await self.find_tool("redeem") or await self.find_tool("xsgd", "credit")
        if not tool_name:
            raise RuntimeError(
                "No redemption tool found on the MCP server. Run "
                "`python -m src.straitsx.list_mcp_tools` to inspect what's available and "
                "update StraitsXCardMCPClient.redeem_xsgd() with the exact tool name/args."
            )
        result = await self.call_tool(tool_name, {"amount": amount_sgd, "tx_hash": tx_hash})
        return _parse_mcp_result(result)


def _parse_mcp_result(result: Any) -> dict:
    """MCP tool results come back as content blocks; this pulls out the
    first parseable JSON text block. Adjust if the server returns a
    different shape (e.g. structured content) once you can inspect it.
    """
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
    raise RuntimeError(f"Could not parse MCP tool result as JSON: {result!r}")