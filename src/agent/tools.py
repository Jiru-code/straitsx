"""LangChain tools wrapping each payment-lifecycle capability.

Each tool catches exceptions and returns error strings (never raises) so the
LLM can reason about failures and explain them to the user.

**Security (Sealed Intent Protocol):**
Every money-moving tool independently validates its arguments against:
  1. The sealed IntentContract (parsed from the user's instruction)
  2. The last verified DiscoveryRecord (stored outside the LLM context)
  3. The spending-policy engine
The LLM cannot bypass these gates regardless of its reasoning.
"""
from __future__ import annotations

import json
from typing import Optional

from langchain_core.tools import tool

from src.agent.discovery import discover_product as _discover, _load_html
from src.chain.avalanche_client import AvalancheXSGDWallet
from src.checkout.browser_checkout import run_checkout
from src.config import settings
from src.policy.spending_policy import policy
from src.security.enforcement import (
    record_discovery,
    record_issuance,
    validate_checkout,
    validate_discovery_url,
    validate_issuance,
)
from src.security.sanitizer import build_discovery_record
from src.straitsx.client import StraitsXClient, VirtualCard


# ------------------------------------------------------------------
# 1. Wallet
# ------------------------------------------------------------------

@tool
def check_wallet_balance() -> str:
    """Check the XSGD stablecoin balance of the agent's Avalanche C-Chain wallet.
    Call this first to confirm funds are available before any purchase."""
    try:
        wallet = AvalancheXSGDWallet()
        if not wallet.is_configured():
            return json.dumps({
                "balance_xsgd": 100.0,
                "wallet_address": "0xSIMULATED",
                "note": "Wallet not configured — using simulated 100.0 XSGD balance for demo.",
            })
        balance = wallet.get_xsgd_balance()
        return json.dumps({
            "balance_xsgd": balance,
            "wallet_address": wallet.address,
        })
    except Exception as exc:
        return json.dumps({"error": f"Failed to check wallet balance: {exc}"})


# ------------------------------------------------------------------
# 2. Product discovery  (SIP Layer 2: Sanitize + Layer 3: URL gate)
# ------------------------------------------------------------------

@tool
def discover_product(url: str) -> str:
    """Scrape a product page to extract its title and SGD price.
    Pass the full URL (https:// or file://) of the product page."""
    try:
        # ---- SIP Layer 3: pre-fetch domain check ----
        url_check = validate_discovery_url(url)
        if not url_check.ok:
            return json.dumps({"error": url_check.reason})

        # ---- Fetch + extract (raw) ----
        listing = _discover(url)

        # ---- SIP Layer 2: sanitize ----
        record = build_discovery_record(
            original_url=url,
            final_url=listing.final_url or url,
            raw_html=listing.raw_html,
            extracted_title=listing.title,
            extracted_price=listing.price_sgd,
        )

        # Store verified record outside LLM context
        record_discovery(record)

        # Build the ToolMessage the LLM sees — sanitized data + security metadata
        result: dict = {
            "title": record.title,
            "price_sgd": record.price_sgd,
            "url": record.final_url,
        }

        # Include security metadata so the LLM can narrate it to the user
        security: dict = {
            "product_category": record.product_category,
            "consistency_score": round(record.consistency_score, 2),
        }
        if record.injection_detected:
            security["injection_detected"] = True
            security["warning"] = (
                "Hidden content with instruction-like patterns was detected "
                "and stripped from this page. Card issuance will be blocked."
            )
        if record.original_url != record.final_url:
            security["redirect_detected"] = True
            security["original_url"] = record.original_url
            security["final_url"] = record.final_url

        result["security"] = security
        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"error": f"Failed to discover product at {url}: {exc}"})


# ------------------------------------------------------------------
# 3. Spending policy
# ------------------------------------------------------------------

@tool
def evaluate_spending_policy(amount_sgd: float, merchant_url: str) -> str:
    """Check whether a purchase of the given amount at the given merchant
    would be approved by the spending-policy engine.  This is a read-only
    check — it does NOT commit the spend."""
    try:
        decision = policy.evaluate(amount_sgd, merchant_url)
        return json.dumps({
            "approved": decision.approved,
            "reason": decision.reason,
        })
    except Exception as exc:
        return json.dumps({"error": f"Policy evaluation failed: {exc}"})


@tool
def get_spending_policy_info() -> str:
    """Return the current spending-policy configuration and how much has been
    spent today.  Useful for explaining limits to the user."""
    return json.dumps({
        "max_transaction_sgd": policy.max_transaction_sgd,
        "daily_limit_sgd": policy.daily_limit_sgd,
        "allowed_merchant_domains": policy.allowed_domains,
        "spent_today_sgd": policy._spent_today,
    })


# ------------------------------------------------------------------
# 4. Card issuance  (SIP Layer 3: Enforce + spending-policy gate)
# ------------------------------------------------------------------

@tool
def issue_virtual_card(
    amount_sgd: float,
    merchant_url: str,
    wallet_address: str = "",
    cardholder_name: str = "AI Agent",
) -> str:
    """Issue a disposable virtual card scoped to the given amount and merchant.

    IMPORTANT: This tool enforces the Sealed Intent Protocol and spending
    policy internally — if either blocks the purchase, the card will NOT be
    issued regardless of what you decide.  Always call
    ``evaluate_spending_policy`` first so you can explain the outcome to the
    user before attempting issuance.
    """
    # ---- SIP Layer 3: contract + discovery enforcement ----
    sip_check = validate_issuance(amount_sgd, merchant_url)
    if not sip_check.ok:
        return json.dumps({
            "issued": False,
            "reason": sip_check.reason,
        })

    # ---- spending-policy hard gate ----
    decision = policy.evaluate(amount_sgd, merchant_url)
    if not decision.approved:
        return json.dumps({
            "issued": False,
            "reason": f"Policy blocked: {decision.reason}",
        })

    try:
        client = StraitsXClient()
        card = client.issue_virtual_card(
            amount_sgd=amount_sgd,
            merchant_domain=merchant_url,
            wallet_address=wallet_address or "0xSIMULATED",
            cardholder_name=cardholder_name,
        )

        # If MCP mode returned card_html (no raw PAN), attempt reveal
        if card.card_html and not card.pan:
            try:
                from src.checkout.card_reveal import reveal_card
                revealed = reveal_card(card.card_html)
                if revealed.pan:
                    card = VirtualCard(
                        amount_sgd=card.amount_sgd,
                        merchant_scope=card.merchant_scope,
                        card_opaque_id=card.card_opaque_id,
                        card_html=card.card_html,
                        settlement_tx=card.settlement_tx,
                        pan=revealed.pan,
                        expiry=revealed.expiry or "",
                        cvv=revealed.cvv or "",
                    )
            except Exception:
                pass  # reveal failed; proceed with whatever we have

        policy.record_spend(amount_sgd)

        # ---- SIP: record issuance for checkout validation ----
        record_issuance(merchant_url, amount_sgd)

        result = {
            "issued": True,
            "amount_sgd": card.amount_sgd,
            "merchant_scope": card.merchant_scope,
        }
        if card.pan:
            result["pan"] = card.pan
            result["expiry"] = card.expiry
            result["cvv"] = card.cvv
        if card.card_id:
            result["card_id"] = card.card_id
        if card.card_opaque_id:
            result["card_opaque_id"] = card.card_opaque_id
        if card.settlement_tx:
            result["settlement_tx"] = card.settlement_tx
        if card.card_html and not card.pan:
            result["note"] = (
                "Card was issued but raw PAN/CVV could not be extracted from "
                "card_html.  The card_html iframe can be viewed manually."
            )
        return json.dumps(result)

    except Exception as exc:
        return json.dumps({"issued": False, "error": f"Card issuance failed: {exc}"})


# ------------------------------------------------------------------
# 5. Checkout execution  (SIP Layer 3: Enforce)
# ------------------------------------------------------------------

@tool
def execute_checkout(
    product_url: str,
    pan: str,
    expiry: str,
    cvv: str,
    amount_sgd: float,
) -> str:
    """Fill in card details on the merchant's checkout page and submit payment.
    Requires PAN, expiry (MM/YY), and CVV from a previously issued card."""
    # ---- SIP Layer 3: checkout must match issued card ----
    sip_check = validate_checkout(product_url, amount_sgd)
    if not sip_check.ok:
        return json.dumps({"success": False, "detail": sip_check.reason})

    try:
        card = VirtualCard(
            pan=pan,
            expiry=expiry,
            cvv=cvv,
            amount_sgd=amount_sgd,
            merchant_scope=product_url,
        )
        receipt = run_checkout(product_url, card)
        return json.dumps({
            "success": receipt.success,
            "detail": receipt.detail,
        })
    except Exception as exc:
        return json.dumps({"success": False, "detail": f"Checkout error: {exc}"})


# ------------------------------------------------------------------
# Public list consumed by the graph builder
# ------------------------------------------------------------------

all_tools = [
    check_wallet_balance,
    discover_product,
    evaluate_spending_policy,
    get_spending_policy_info,
    issue_virtual_card,
    execute_checkout,
]
