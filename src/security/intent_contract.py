"""Layer 1: SEAL — parse the user's instruction into an immutable IntentContract.

The contract is created deterministically (regex, not LLM) so it cannot be
prompt-injected.  It is stored as a module-level singleton OUTSIDE the LLM's
message context — the LLM never sees it, so it cannot override it.

Every money-moving tool validates its arguments against this contract.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Optional

# ---- stopwords for keyword extraction (common English + purchase verbs) ----

_STOPWORDS: frozenset[str] = frozenset(
    "a an the and or but in on at to for of is it i me my we our you your "
    "this that these those from with by be am are was were been being "
    "buy purchase get order find shop pay checkout item items product products "
    "some any please can could would should will shall do does did "
    "want need like looking something anything".split()
)

# ---- restricted product categories (always blocked) ----

_RESTRICTED_CATEGORIES: frozenset[str] = frozenset({
    "gift card", "gift voucher", "e-gift", "egift", "prepaid card",
    "store credit", "cash card", "cryptocurrency", "bitcoin", "btc",
    "ethereum", "eth", "crypto token", "money order", "wire transfer",
    "prepaid debit", "visa gift", "mastercard gift",
})


@dataclass(frozen=True)
class IntentContract:
    """Immutable, machine-readable specification of what the agent is
    authorized to do.  Created once from the user's instruction; never
    modified afterwards.
    """
    raw_instruction: str
    product_keywords: tuple[str, ...]
    max_price_sgd: Optional[float]
    quantity: int
    merchant_url: Optional[str]
    restricted_categories: frozenset[str]
    contract_hash: str


# ---- module-level singleton ----

_contract: Optional[IntentContract] = None


def get_contract() -> Optional[IntentContract]:
    """Return the current sealed contract, or ``None`` if not yet sealed."""
    return _contract


def seal_intent(instruction: str) -> IntentContract:
    """Parse *instruction* into an ``IntentContract`` and store it.

    This must be called **before** the agent loop starts — i.e. before
    any external content enters the LLM's context.  Calling it again
    replaces the previous contract (for multi-turn sessions where each
    user message is a new purchase instruction).
    """
    global _contract

    keywords = _extract_keywords(instruction)
    max_price = _extract_max_price(instruction)
    quantity = _extract_quantity(instruction)
    url = _extract_url(instruction)

    # Build a deterministic hash of the contract fields
    hash_input = json.dumps({
        "instruction": instruction,
        "keywords": keywords,
        "max_price": max_price,
        "quantity": quantity,
        "url": url,
    }, sort_keys=True)
    contract_hash = hashlib.sha256(hash_input.encode()).hexdigest()

    _contract = IntentContract(
        raw_instruction=instruction,
        product_keywords=tuple(keywords),
        max_price_sgd=max_price,
        quantity=quantity,
        merchant_url=url,
        restricted_categories=_RESTRICTED_CATEGORIES,
        contract_hash=contract_hash,
    )
    return _contract


# ---- deterministic parsers (regex, no LLM) ----

def _extract_keywords(text: str) -> list[str]:
    """Tokenize, lowercase, remove stopwords / URLs / numbers."""
    # Strip URLs first so they don't contribute tokens
    text = re.sub(r"https?://\S+|file://\S+", "", text)
    tokens = re.findall(r"[a-zA-Z]+", text.lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


def _extract_max_price(text: str) -> Optional[float]:
    """Look for price caps like 'under 40 SGD', 'below $50', 'max 30'."""
    patterns = [
        r"(?:under|below|less\s+than|max(?:imum)?|budget(?:\s+of)?|up\s+to|at\s+most)\s+"
        r"(?:SGD|S\$|\$)?\s*([\d,.]+)\s*(?:SGD|S\$|dollars?|sgd)?",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                continue
    return None


def _extract_quantity(text: str) -> int:
    """Look for explicit quantities like '3 of', '2 pieces', '5 items'."""
    m = re.search(
        r"(\d+)\s+(?:of|pieces?|units?|items?|pcs?|copies|sets?)\b",
        text,
        re.IGNORECASE,
    )
    if m:
        try:
            qty = int(m.group(1))
            return max(qty, 1)
        except ValueError:
            pass
    return 1


def _extract_url(text: str) -> Optional[str]:
    """Pull the first http(s):// or file:// URL from the text."""
    m = re.search(r"(https?://\S+|file://\S+)", text)
    return m.group(1) if m else None


def _reset() -> None:
    """Clear the sealed contract (for tests only)."""
    global _contract
    _contract = None
