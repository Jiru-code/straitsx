"""StraitsX integration, with three interchangeable backends selected by
STRAITSX_MODE in .env:

- "mock" (default) -- synthetic responses, no network needed. Good for
  building/demoing the rest of the agent before you have any credentials.
- "mcp" -- talks to StraitsX's card-issuance MCP server (sandbox or
  production, see mcp_client.py). This is the recommended path if you
  don't have a StraitsX business account, since the sandbox server handles
  StraitsX-side auth for you.
- "rest" -- the raw StraitsX REST API (see https://docs.straitsx.com/).
  Requires API key/secret tied to a business account. Endpoint paths below
  are marked TODO since they need real sandbox docs to confirm.

Whichever mode is active, callers always get back the same VirtualCard /
dict shapes -- the rest of the agent graph doesn't need to know which
backend is in use.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass

import httpx

from src.config import settings


@dataclass
class VirtualCard:
    # Always present
    amount_sgd: float
    merchant_scope: str

    # Present for the real sandbox/production MCP flow
    card_opaque_id: str = ""
    card_html: str = ""          # one-time iframe URL/markup showing the card
    settlement_tx: str = ""      # on-chain tx hash that paid for the card

    # Present for mock mode, or after a successful reveal step (see
    # checkout/card_reveal.py) -- NOT returned directly by the sandbox API.
    card_id: str = ""
    pan: str = ""
    expiry: str = ""
    cvv: str = ""


def _pick(data: dict, *keys: str, default: str = "") -> str:
    """Best-effort field lookup across possible key-naming conventions in
    an MCP/REST response (e.g. card_number vs pan vs number).
    """
    for k in keys:
        if k in data and data[k] not in (None, ""):
            return str(data[k])
    return default


class StraitsXClient:
    def __init__(self) -> None:
        self.mode = settings.straitsx_mode  # "mock" | "mcp" | "rest"

        # REST setup (only used in "rest" mode)
        self.base_url = settings.straitsx_api_base
        self.api_key = settings.straitsx_api_key
        self.api_secret = settings.straitsx_api_secret
        self._http = httpx.Client(base_url=self.base_url, timeout=15.0)

        # MCP setup (only used in "mcp" mode)
        self._mcp = None
        if self.mode == "mcp":
            from src.straitsx.mcp_client import StraitsXCardMCPClient

            self._mcp = StraitsXCardMCPClient()

    def _auth_headers(self) -> dict:
        # TODO: replace with StraitsX's actual REST auth scheme (API key +
        # HMAC signature per their docs) once you have business-account credentials.
        return {"X-API-KEY": self.api_key}

    # ------------------------------------------------------------------
    # Redemption
    # ------------------------------------------------------------------
    def redeem_xsgd(self, amount_sgd: float, deposit_tx_hash: str) -> dict:
        """Confirms an on-chain XSGD transfer and redeems it into spendable
        balance ahead of card issuance.
        """
        if self.mode == "mock":
            return {
                "status": "credited",
                "amount_sgd": amount_sgd,
                "tx_hash": deposit_tx_hash,
                "reference": f"mock-redeem-{uuid.uuid4().hex[:8]}",
            }

        if self.mode == "mcp":
            return asyncio.run(self._mcp.redeem_xsgd(amount_sgd, deposit_tx_hash))

        # rest mode -- TODO: real endpoint, e.g. POST /v1/redemptions
        resp = self._http.post(
            "/v1/redemptions",
            headers=self._auth_headers(),
            json={"amount": amount_sgd, "currency": "SGD", "tx_hash": deposit_tx_hash},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Card issuance
    # ------------------------------------------------------------------
    def issue_virtual_card(
        self,
        amount_sgd: float,
        merchant_domain: str,
        wallet_address: str = "",
        cardholder_name: str = "",
    ) -> VirtualCard:
        """Requests a disposable virtual card scoped to a specific spend
        amount and merchant.

        In "mcp" mode this is a two-step flow: the MCP tool call returns a
        `cardapi` URL + x402 payment requirements (no card yet), then a raw
        HTTP call against that URL completes the x402 challenge/response
        before the card is actually issued.
        """
        if self.mode == "mock":
            return VirtualCard(
                card_id=f"vc_{uuid.uuid4().hex[:10]}",
                pan="4" + "".join(str((i * 7 + 3) % 10) for i in range(15)),
                expiry=time.strftime("%m/%y", time.localtime(time.time() + 60 * 60 * 24 * 30)),
                cvv=f"{uuid.uuid4().int % 1000:03d}",
                amount_sgd=amount_sgd,
                merchant_scope=merchant_domain,
                card_opaque_id=f"mock_opaque_{uuid.uuid4().hex[:8]}",
                settlement_tx="0xMOCKSETTLEMENTTX",
            )

        if self.mode == "mcp":
            if not wallet_address or not cardholder_name:
                raise ValueError("mcp mode requires wallet_address and cardholder_name to issue a card.")

            # Step 1: ask the MCP tool to prepare issuance -- this hands
            # back a cardapi URL + x402 requirements, not a card.
            prep = asyncio.run(
                self._mcp.get_card_sandbox(
                    wallet_address=wallet_address,
                    cardholder_name=cardholder_name,
                    amount_sgd=amount_sgd,
                )
            )
            cardapi_url = _pick(prep, "cardapi", "cardapi_url", "url")
            if not cardapi_url:
                raise RuntimeError(
                    f"get_card_sandbox response had no cardapi URL to call: {prep!r}"
                )

            # Step 2: complete the x402 challenge/response against that URL.
            from src.straitsx.x402_client import pay_and_fetch

            result = pay_and_fetch(
                cardapi_url=cardapi_url,
                wallet_private_key=settings.wallet_private_key,
                wallet_address=wallet_address,
                chain_id=settings.avalanche_chain_id,
                # Unconfirmed whether cardapi needs these restated in the body
                # (the URL may already encode them) -- harmless either way,
                # but drop this if the endpoint errors on an unexpected body.
                json_body={
                    "wallet_address": wallet_address,
                    "cardholder_name": cardholder_name,
                    "amount_sgd": amount_sgd,
                },
            )
            return VirtualCard(
                amount_sgd=amount_sgd,
                merchant_scope=merchant_domain,
                card_opaque_id=_pick(result, "card_opaque_id"),
                card_html=_pick(result, "card_html"),
                settlement_tx=_pick(result, "settlement_tx"),
            )

        # rest mode -- TODO: real endpoint, e.g. POST /v1/cards/virtual
        resp = self._http.post(
            "/v1/cards/virtual",
            headers=self._auth_headers(),
            json={"amount": amount_sgd, "currency": "SGD", "merchant_domain": merchant_domain},
        )
        resp.raise_for_status()
        data = resp.json()
        return VirtualCard(
            card_id=_pick(data, "card_id", "id"),
            pan=_pick(data, "pan", "card_number"),
            expiry=_pick(data, "expiry"),
            cvv=_pick(data, "cvv"),
            amount_sgd=amount_sgd,
            merchant_scope=merchant_domain,
        )

    # ------------------------------------------------------------------
    def get_deposit_address(self) -> str:
        """Blockchain address to send XSGD to for crediting the StraitsX
        account, ahead of redemption.
        """
        if self.mode == "mock":
            return "0x000000000000000000000000000000MOCKDEP"
        if self.mode == "mcp":
            tool_name = asyncio.run(self._mcp.find_tool("deposit", "address"))
            if not tool_name:
                raise RuntimeError(
                    "No deposit-address tool found on the MCP server. Run "
                    "`python -m src.straitsx.list_mcp_tools` to see what's available."
                )
            result = asyncio.run(self._mcp.call_tool(tool_name, {"asset": "XSGD", "network": "avalanche"}))
            from src.straitsx.mcp_client import _parse_mcp_result

            return _pick(_parse_mcp_result(result), "address", "deposit_address")
        # TODO: real endpoint, e.g. GET /v1/accounts/deposit-address?asset=XSGD&network=avalanche
        resp = self._http.get(
            "/v1/accounts/deposit-address",
            headers=self._auth_headers(),
            params={"asset": "XSGD", "network": "avalanche"},
        )
        resp.raise_for_status()
        return resp.json()["address"]