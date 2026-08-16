"""Layer 3: ENFORCE — every money-moving tool validates its arguments
against the sealed intent contract and the last verified discovery.

Module-level state (``_last_discovery``, ``_last_issuance``) lives OUTSIDE
the LLM's message context.  The LLM cannot read or modify it — only the
deterministic tool code can.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from src.config import settings
from src.security.intent_contract import get_contract
from src.security.sanitizer import DiscoveryRecord


@dataclass
class ValidationResult:
    ok: bool
    reason: str


# ---- module-level state (outside LLM context) ----

_last_discovery: Optional[DiscoveryRecord] = None
_last_issuance: Optional[dict] = None


def get_last_discovery() -> Optional[DiscoveryRecord]:
    return _last_discovery


def record_discovery(record: DiscoveryRecord) -> None:
    global _last_discovery
    _last_discovery = record


def record_issuance(merchant_url: str, amount_sgd: float) -> None:
    global _last_issuance
    _last_issuance = {"merchant_url": merchant_url, "amount_sgd": amount_sgd}


# ---- URL helpers ----

def _domain_of(url: str) -> str:
    """Extract the domain from a URL, handling file:// as 'localhost'."""
    parsed = urlparse(url)
    if parsed.scheme == "file":
        return "localhost"
    return (parsed.netloc or url).split(":")[0]


def _urls_match(a: str, b: str) -> bool:
    """Check if two URLs point to the same location.

    Compares domains and paths.  Lenient on trailing slashes and
    file:// vs bare paths.
    """
    if a == b:
        return True
    # Normalize: both as parsed URLs
    pa, pb = urlparse(a), urlparse(b)
    if pa.scheme == "file" and pb.scheme == "file":
        return pa.path.rstrip("/") == pb.path.rstrip("/")
    return (
        _domain_of(a) == _domain_of(b)
        and pa.path.rstrip("/") == pb.path.rstrip("/")
    )


def _keyword_overlap(contract_keywords: tuple[str, ...], title: str) -> float:
    """Fraction of contract keywords found in the title (0–1)."""
    if not contract_keywords:
        return 1.0  # no keywords to check → permissive
    title_tokens = set(re.findall(r"[a-z]+", title.lower()))
    if not title_tokens:
        return 0.0
    matched = sum(1 for kw in contract_keywords if kw in title_tokens)
    return matched / len(contract_keywords)


# ---- validation gates ----

def validate_discovery_url(url: str) -> ValidationResult:
    """Check the URL's domain against the allowlist BEFORE fetching.

    file:// URLs pass automatically (local fixtures).
    """
    if not settings.sip_enabled:
        return ValidationResult(True, "SIP disabled")

    parsed = urlparse(url)
    if parsed.scheme == "file" or (not parsed.scheme and not url.startswith("http")):
        return ValidationResult(True, "Local file URL — allowed.")

    domain = _domain_of(url)
    allowed = settings.allowed_merchant_domains
    if allowed and domain not in allowed:
        return ValidationResult(
            False,
            f"SIP: Domain '{domain}' is not on the allowed merchant list "
            f"({', '.join(allowed)}). Discovery blocked before fetching.",
        )
    return ValidationResult(True, f"Domain '{domain}' is on the allow-list.")


def validate_issuance(amount_sgd: float, merchant_url: str) -> ValidationResult:
    """The big gate — validates issuance arguments against the sealed
    contract AND the last discovery record.

    Checks (in order):
      1. Discovery must have happened first
      2. merchant_url must match last discovery's final_url
      3. amount_sgd must match last discovery's price
      4. Product category must not be restricted
      5. No injection detected (if sip_block_on_injection)
      6. Signal consistency must exceed threshold
      7. Keyword overlap with intent must exceed threshold
      8. Price must be within intent's max_price
    """
    if not settings.sip_enabled:
        return ValidationResult(True, "SIP disabled")

    disc = _last_discovery
    contract = get_contract()

    # 1. Must discover before issuing
    if disc is None:
        return ValidationResult(
            False,
            "SIP: No product has been discovered yet. "
            "Call discover_product before issuing a card.",
        )

    # 2. merchant_url must match the discovered URL
    if not _urls_match(merchant_url, disc.final_url):
        return ValidationResult(
            False,
            f"SIP: merchant_url '{merchant_url}' does not match the last "
            f"discovered product URL '{disc.final_url}'. "
            "The card can only be issued for the product that was discovered.",
        )

    # 3. amount must match discovered price
    if abs(amount_sgd - disc.price_sgd) > 0.01:
        return ValidationResult(
            False,
            f"SIP: amount_sgd ({amount_sgd:.2f}) does not match the discovered "
            f"price ({disc.price_sgd:.2f}). The card amount must match the "
            "product price from discovery.",
        )

    # 4. Product category must not be restricted
    if contract and disc.product_category in {
        cat.replace(" ", "_") for cat in contract.restricted_categories
    }:
        return ValidationResult(
            False,
            f"SIP: Product category '{disc.product_category}' is restricted. "
            f"Discovered title: '{disc.title}'. This type of product cannot "
            "be purchased by the agent.",
        )

    # 5. No injection detected
    if settings.sip_block_on_injection and disc.injection_detected:
        details = "; ".join(disc.injection_details[:3])
        return ValidationResult(
            False,
            f"SIP: Prompt-injection patterns were detected on the product page. "
            f"Details: {details}. Card issuance blocked for safety.",
        )

    # 6. Signal consistency
    if disc.consistency_score < settings.sip_consistency_threshold:
        return ValidationResult(
            False,
            f"SIP: Product page signal consistency ({disc.consistency_score:.2f}) "
            f"is below threshold ({settings.sip_consistency_threshold}). "
            "The page's metadata signals disagree — possible typosquat or "
            "manipulated listing.",
        )

    # 7. Keyword overlap with intent
    if contract and contract.product_keywords:
        overlap = _keyword_overlap(contract.product_keywords, disc.title)
        if overlap < settings.sip_keyword_threshold:
            return ValidationResult(
                False,
                f"SIP: Product title '{disc.title}' has low keyword overlap "
                f"({overlap:.0%}) with the original intent "
                f"(keywords: {', '.join(contract.product_keywords)}). "
                "This may be a substitution — the discovered product does not "
                "match what was requested.",
            )

    # 8. Price within intent max
    if contract and contract.max_price_sgd is not None:
        if amount_sgd > contract.max_price_sgd:
            return ValidationResult(
                False,
                f"SIP: Price {amount_sgd:.2f} SGD exceeds the intent's maximum "
                f"of {contract.max_price_sgd:.2f} SGD.",
            )

    return ValidationResult(True, "SIP: All checks passed.")


def validate_checkout(product_url: str, amount_sgd: float) -> ValidationResult:
    """Verify the checkout target matches what was issued."""
    if not settings.sip_enabled:
        return ValidationResult(True, "SIP disabled")

    if _last_issuance is None:
        return ValidationResult(
            False,
            "SIP: No card has been issued yet. Issue a card before checkout.",
        )

    issued_url = _last_issuance["merchant_url"]
    issued_amt = _last_issuance["amount_sgd"]

    if not _urls_match(product_url, issued_url):
        return ValidationResult(
            False,
            f"SIP: Checkout URL '{product_url}' does not match the issued "
            f"card's merchant scope '{issued_url}'.",
        )

    if abs(amount_sgd - issued_amt) > 0.01:
        return ValidationResult(
            False,
            f"SIP: Checkout amount ({amount_sgd:.2f}) does not match the "
            f"issued card amount ({issued_amt:.2f}).",
        )

    return ValidationResult(True, "SIP: Checkout matches issued card.")


def reset() -> None:
    """Clear all module-level state (for tests)."""
    global _last_discovery, _last_issuance
    _last_discovery = None
    _last_issuance = None
