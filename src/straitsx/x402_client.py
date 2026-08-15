"""Handles the x402 payment flow against StraitsX's cardapi endpoint.

Flow:
  1. POST the cardapi URL returned by the `get_card_sandbox` MCP tool.
  2. Expect HTTP 402 with a base64-encoded `PAYMENT-REQUIRED` header
     containing the challenge (payTo, amount, asset, network, EIP-712 info).
  3. Sign an EIP-3009 TransferWithAuthorization for that amount of XSGD.
  4. Retry with a base64-encoded `PAYMENT-SIGNATURE` header.
  5. On success, the response body contains card_opaque_id, card_html, and
     settlement_tx.
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


def _parse_challenge(accept: dict, x402_version: int = 1) -> X402Challenge:
    try:
        extra = accept.get("extra", {}) or {}
        return X402Challenge(
            pay_to=accept["payTo"],
            amount_atomic=int(accept["amount"]),
            asset_address=accept["asset"],
            token_name=extra.get("name", "XSGD"),
            token_version=extra.get("version", "1"),
            scheme=accept.get("scheme", "exact"),
            network=accept.get("network", ""),
            x402_version=x402_version,
            valid_before=int(time.time()) + int(accept.get("maxTimeoutSeconds", 3600)),
            chain_id=accept.get("chainId"),
        )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise X402PaymentError(
            f"Unrecognized 402 challenge shape: {accept!r}. "
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

        payment_required = first.headers.get("payment-required", "")
        if payment_required:
            challenge_data = json.loads(base64.b64decode(payment_required))
        else:
            challenge_data = first.json()

        x402_version = 1
        if isinstance(challenge_data, dict) and "accepts" in challenge_data:
            x402_version = challenge_data.get("x402Version", 1)
            accept = challenge_data["accepts"][0]
        elif isinstance(challenge_data, list):
            accept = challenge_data[0]
        else:
            accept = challenge_data

        challenge = _parse_challenge(accept, x402_version)

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
            "payload": auth,
            "accepted": accept,
        }
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