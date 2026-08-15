"""Handles the x402 payment flow against StraitsX's cardapi endpoint.

Flow:
  1. POST the cardapi URL returned by the `get_card_sandbox` (or production
     equivalent) MCP tool.
  2. Expect an HTTP 402 response describing what payment is required (price,
     pay-to address, asset contract, EIP-712 domain info for signing).
  3. Sign an EIP-3009 TransferWithAuthorization for that amount of XSGD.
  4. Retry the request with a `PAYMENT-SIGNATURE` header carrying the signed
     authorization.
  5. On success, the response body contains card_opaque_id, card_html, and
     settlement_tx.

CAVEAT: the exact JSON shape of the 402 challenge body below follows the
published x402 "exact" scheme spec (x402.org / Coinbase's x402 whitepaper):
`{"x402Version": 1, "accepts": [{"scheme","network","payTo","asset",
"maxAmountRequired","extra": {"name","version"}, ...}]}`. This was written
without being able to reach card.straitsx.ai to confirm the live response
matches that shape exactly -- if StraitsX's cardapi differs, adjust
`_parse_challenge()` below accordingly. Run a real request against sandbox
and print `first.json()` to check before relying on this in a demo.
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from src.straitsx.eip3009 import build_transfer_with_authorization


class X402PaymentError(RuntimeError):
    pass


@dataclass
class X402Challenge:
    pay_to: str
    amount_atomic: int
    asset_address: str
    token_name: str
    token_version: str
    scheme: str
    network: str
    x402_version: int
    valid_before: int
    chain_id: Optional[int] = None


def _parse_challenge(body: dict) -> X402Challenge:
    try:
        accept = body["accepts"][0]
        extra = accept.get("extra", {}) or {}
        return X402Challenge(
            pay_to=accept["payTo"],
            amount_atomic=int(accept["amount"]),
            asset_address=accept["asset"],
            token_name=extra.get("name", "XSGD"),
            token_version=extra.get("version", "1"),
            scheme=accept.get("scheme", "exact"),
            network=accept.get("network", ""),
            x402_version=body.get("x402Version", 1),
            valid_before=int(time.time()) + int(accept.get("maxTimeoutSeconds", 3600)),
            chain_id=accept.get("chainId"),
        )
    except (KeyError, IndexError, TypeError) as exc:
        raise X402PaymentError(
            f"Unrecognized 402 challenge shape from cardapi: {body!r}. "
            "Update x402_client._parse_challenge() to match the real response."
        ) from exc


def pay_and_fetch(
    cardapi_url: str,
    wallet_private_key: str,
    wallet_address: str,
    chain_id: int,
    timeout: float = 30.0,
    json_body: Optional[dict] = None,
) -> dict:
    """Completes the x402 challenge/response flow and returns the parsed
    JSON body of the successful response (card_opaque_id, card_html,
    settlement_tx).

    Uses POST -- the real cardapi endpoint returns 405 Method Not Allowed
    on GET. `json_body` lets you pass along the same issuance params
    (wallet_address/cardholder_name/amount_sgd) again if the endpoint turns
    out to need them restated in the body; unconfirmed either way since
    this couldn't be tested against the live server.
    """
    with httpx.Client(timeout=timeout) as client:
        first = client.post(cardapi_url, json=json_body)

        if first.status_code != 402:
            if first.status_code >= 400:
                try:
                    detail = first.json()
                except ValueError:
                    detail = first.text
                raise X402PaymentError(f"cardapi returned status {first.status_code} before any payment challenge: {detail!r}")
            return first.json()  # no payment challenge needed / already settled

        challenge = _parse_challenge(first.json())

        auth = build_transfer_with_authorization(
            private_key=wallet_private_key,
            token_name=challenge.token_name,
            token_version=challenge.token_version,
            chain_id=challenge.chain_id or chain_id,
            verifying_contract=challenge.asset_address,
            from_address=wallet_address,
            to_address=challenge.pay_to,
            value=challenge.amount_atomic,
            valid_before=challenge.valid_before,
        )

        payment_payload = {
            "x402Version": challenge.x402_version,
            "scheme": challenge.scheme,
            "network": challenge.network,
            "payload": auth,
        }
        # x402 typically base64-encodes the JSON payment payload into the header.
        payment_header = base64.b64encode(json.dumps(payment_payload).encode()).decode()

        second = client.post(cardapi_url, json=json_body, headers={"PAYMENT-SIGNATURE": payment_header})
        if second.status_code != 200:
            try:
                detail = second.json()
            except ValueError:
                detail = second.text
            raise X402PaymentError(
                f"cardapi rejected the payment (status {second.status_code}): {detail!r}. "
                "This means the PAYMENT-SIGNATURE payload didn't verify -- check `detail` above "
                "for the server's specific reason (bad encoding, wrong signature format, "
                "insufficient testnet XSGD balance, expired validBefore, etc.) before changing "
                "anything blind."
            )
        return second.json()