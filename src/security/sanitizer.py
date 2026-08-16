"""Layer 2: SANITIZE — strip injection from discovery results before
they enter the LLM's message context.

Two defenses:
  1. Injection detection — scan hidden elements and the title for patterns
     that look like prompt-injection instructions.
  2. Multi-signal consistency — extract product identity from every
     available signal source (meta tags, JSON-LD, visible text, microdata)
     independently and score how well they agree.  A legitimate page has
     high agreement; a typosquat that stuffs one signal but not others
     will score low.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup, Tag

from src.config import settings

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class SignalExtraction:
    source: str  # meta_product | og | json_ld | visible_text | microdata
    title: Optional[str] = None
    price: Optional[float] = None


@dataclass
class DiscoveryRecord:
    """Verified, sanitized discovery result stored OUTSIDE the LLM context."""
    title: str
    price_sgd: float
    final_url: str
    original_url: str
    product_category: str
    injection_detected: bool
    injection_details: list[str] = field(default_factory=list)
    consistency_score: float = 1.0
    page_fingerprint: str = ""
    signals: list[SignalExtraction] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Injection pattern detection
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS: list[re.Pattern] = [
    # Instruction override
    re.compile(
        r"(?:ignore|disregard|forget|override|bypass|skip)\s+"
        r"(?:(?:all|any|the|your)\s+)?"
        r"(?:previous|prior|above|earlier|existing|current)\s+"
        r"(?:instructions?|rules?|guidelines?|constraints?|prompts?|policies?|limits?)",
        re.IGNORECASE,
    ),
    # Authority claiming
    re.compile(
        r"you\s+(?:are|must|should|need\s+to|have\s+to)\s+"
        r"(?:now\s+)?(?:authorized?|allowed|instructed|required|permitted)",
        re.IGNORECASE,
    ),
    # Tool call injection
    re.compile(
        r"(?:call|invoke|execute|run|use|trigger)\s+"
        r"(?:the\s+)?(?:tool\s+)?"
        r"(?:issue_virtual_card|execute_checkout|discover_product|check_wallet|evaluate_spending)",
        re.IGNORECASE,
    ),
    # Payment redirection
    re.compile(
        r"(?:send|transfer|pay|wire|remit|forward)\s+"
        r"(?:the\s+)?(?:money|funds?|payment|crypto|coins?|tokens?|balance)\s+"
        r"(?:to|into|towards)",
        re.IGNORECASE,
    ),
    # Parameter injection
    re.compile(r"merchant_url\s*=", re.IGNORECASE),
    re.compile(r"amount_sgd\s*=", re.IGNORECASE),
    # Role / system impersonation
    re.compile(r"(?:^|\n)\s*(?:SYSTEM|ADMIN|IMPORTANT|OVERRIDE|INSTRUCTION)\s*:", re.IGNORECASE),
    # Bare Ethereum addresses in text context (suspicious in a product title)
    re.compile(r"0x[a-fA-F0-9]{40}"),
]


def _matches_injection(text: str) -> list[str]:
    """Return human-readable descriptions of all injection patterns matched."""
    hits: list[str] = []
    for pat in _INJECTION_PATTERNS:
        m = pat.search(text)
        if m:
            hits.append(f"Pattern matched: '{m.group()[:80]}'")
    return hits


# ---------------------------------------------------------------------------
# Hidden-element injection scanning
# ---------------------------------------------------------------------------

_HIDDEN_STYLE_PATTERNS: list[re.Pattern] = [
    re.compile(r"display\s*:\s*none", re.IGNORECASE),
    re.compile(r"visibility\s*:\s*hidden", re.IGNORECASE),
    re.compile(r"opacity\s*:\s*0(?:[;\s]|$)", re.IGNORECASE),
    re.compile(r"font-size\s*:\s*0", re.IGNORECASE),
    re.compile(r"height\s*:\s*0", re.IGNORECASE),
    re.compile(r"width\s*:\s*0", re.IGNORECASE),
    re.compile(r"overflow\s*:\s*hidden", re.IGNORECASE),
    re.compile(r"position\s*:\s*absolute.*?(?:left|top)\s*:\s*-\d", re.IGNORECASE | re.DOTALL),
]


def _is_hidden(tag: Tag) -> bool:
    """Heuristic: is this element visually hidden?"""
    style = tag.get("style", "")
    if isinstance(style, str) and any(p.search(style) for p in _HIDDEN_STYLE_PATTERNS):
        return True
    if tag.get("aria-hidden") == "true":
        return True
    classes = tag.get("class", [])
    if isinstance(classes, list):
        classes_str = " ".join(classes).lower()
    else:
        classes_str = str(classes).lower()
    if "hidden" in classes_str or "sr-only" in classes_str or "visually-hidden" in classes_str:
        return True
    return False


def detect_injections(soup: BeautifulSoup) -> tuple[bool, list[str]]:
    """Scan the full page for injection patterns, especially in hidden elements.

    Returns (detected, detail_messages).
    """
    details: list[str] = []

    # Scan hidden elements specifically
    for tag in soup.find_all(True):
        if not _is_hidden(tag):
            continue
        hidden_text = tag.get_text(" ", strip=True)
        if not hidden_text:
            continue
        hits = _matches_injection(hidden_text)
        for h in hits:
            details.append(f"Hidden element <{tag.name}>: {h}")

    return (len(details) > 0, details)


# ---------------------------------------------------------------------------
# Title sanitization
# ---------------------------------------------------------------------------

def sanitize_title(raw_title: str) -> tuple[str, bool, list[str]]:
    """Return (clean_title, injection_detected, matched_patterns).

    Strips embedded URLs, control characters, instruction-like patterns,
    and truncates to the configured max length.
    """
    max_len = settings.sip_max_title_length

    # Strip embedded URLs
    clean = re.sub(r"https?://\S+", "", raw_title)
    clean = re.sub(r"file://\S+", "", clean)

    # Strip control characters and excessive whitespace
    clean = re.sub(r"[\x00-\x1f\x7f]", " ", clean)
    clean = re.sub(r"\n+", " ", clean)
    clean = re.sub(r"\s{2,}", " ", clean).strip()

    # Detect injection in original title
    hits = _matches_injection(raw_title)

    # Truncate
    if len(clean) > max_len:
        clean = clean[:max_len].rsplit(" ", 1)[0] + "…"

    return clean, len(hits) > 0, hits


# ---------------------------------------------------------------------------
# Multi-signal extraction
# ---------------------------------------------------------------------------

def _meta_content(soup: BeautifulSoup, name: str) -> Optional[str]:
    """Get content attr from <meta name=... > or <meta property=...>."""
    tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
    if tag:
        val = tag.get("content")
        return str(val) if val else None
    return None


def _safe_float(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    try:
        return float(str(s).replace(",", ""))
    except (ValueError, TypeError):
        return None


def extract_all_signals(soup: BeautifulSoup) -> list[SignalExtraction]:
    """Extract product identity from every independent signal source."""
    signals: list[SignalExtraction] = []

    # 1. Product meta tags
    meta_title = _meta_content(soup, "product:title") or _meta_content(soup, "og:title")
    meta_price = _safe_float(_meta_content(soup, "product:price:amount"))
    if meta_title or meta_price is not None:
        signals.append(SignalExtraction(source="meta_product", title=meta_title, price=meta_price))

    # 2. Open Graph (og:) — separate from product: tags
    og_title = _meta_content(soup, "og:title")
    if og_title and og_title != meta_title:
        signals.append(SignalExtraction(source="og", title=og_title))

    # 3. JSON-LD structured data
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        # Handle both single objects and arrays
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            jld_title = item.get("name")
            jld_price = None
            offers = item.get("offers")
            if isinstance(offers, dict):
                jld_price = _safe_float(offers.get("price"))
            elif isinstance(offers, list) and offers:
                jld_price = _safe_float(offers[0].get("price"))
            if jld_title or jld_price is not None:
                signals.append(SignalExtraction(source="json_ld", title=jld_title, price=jld_price))

    # 4. Microdata (itemprop)
    micro_name = soup.find(attrs={"itemprop": "name"})
    micro_price = soup.find(attrs={"itemprop": "price"})
    if micro_name or micro_price:
        signals.append(SignalExtraction(
            source="microdata",
            title=micro_name.get_text(strip=True) if micro_name else None,
            price=_safe_float(
                micro_price.get("content") or micro_price.get_text(strip=True)
                if micro_price else None
            ),
        ))

    # 5. Visible text heuristic (h1 / #product-title + #product-price or first SGD price)
    vis_title_tag = soup.find(id="product-title") or soup.find("h1")
    vis_title = vis_title_tag.get_text(strip=True) if vis_title_tag else None

    vis_price_tag = soup.find(id="product-price")
    if vis_price_tag:
        price_text = vis_price_tag.get_text(strip=True)
    else:
        price_text = soup.get_text()
    price_match = re.search(r"(?:SGD|S\$)\s*([\d,]+\.\d{2})", price_text)
    vis_price = float(price_match.group(1).replace(",", "")) if price_match else None

    if vis_title or vis_price is not None:
        signals.append(SignalExtraction(source="visible_text", title=vis_title, price=vis_price))

    return signals


# ---------------------------------------------------------------------------
# Consistency scoring
# ---------------------------------------------------------------------------

def _title_similarity(a: str, b: str) -> float:
    """Jaccard similarity on lowercased word tokens."""
    tokens_a = set(re.findall(r"[a-z]+", a.lower()))
    tokens_b = set(re.findall(r"[a-z]+", b.lower()))
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def score_consistency(signals: list[SignalExtraction]) -> float:
    """Pairwise agreement across all extracted signals.

    Returns 0–1 where 1 = perfect agreement.  With fewer than 2 signals,
    returns 1.0 (no disagreement possible).
    """
    if len(signals) < 2:
        return 1.0

    title_scores: list[float] = []
    price_scores: list[float] = []

    for i, a in enumerate(signals):
        for b in signals[i + 1:]:
            if a.title and b.title:
                title_scores.append(_title_similarity(a.title, b.title))
            if a.price is not None and b.price is not None:
                price_scores.append(1.0 if abs(a.price - b.price) < 0.01 else 0.0)

    all_scores = title_scores + price_scores
    if not all_scores:
        return 1.0
    return sum(all_scores) / len(all_scores)


# ---------------------------------------------------------------------------
# Product category classification
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "gift_card": [
        "gift card", "gift voucher", "e-gift", "egift", "store credit",
        "prepaid card", "cash card", "visa gift", "mastercard gift",
        "amazon gift", "itunes gift", "google play gift",
    ],
    "cryptocurrency": [
        "bitcoin", "btc", "ethereum", "eth", "crypto", "token sale",
        "nft", "altcoin", "defi", "stablecoin",
    ],
    "cash_equivalent": [
        "money order", "wire transfer", "prepaid debit", "cash voucher",
    ],
    "electronics": [
        "earbuds", "headphones", "speaker", "laptop", "phone", "tablet",
        "camera", "monitor", "keyboard", "mouse", "charger", "cable",
        "smartwatch", "drone", "router", "printer",
    ],
    "apparel": [
        "shirt", "pants", "dress", "jacket", "shoes", "sneakers",
        "hat", "socks", "hoodie", "shorts",
    ],
}


def classify_category(title: str) -> str:
    """Keyword-based product category classification.

    Checks restricted categories first (gift_card, cryptocurrency,
    cash_equivalent), then informational ones.  Returns the first match
    or ``'general'``.
    """
    lower = title.lower()
    # Check restricted categories first (order matters)
    for cat in ("gift_card", "cryptocurrency", "cash_equivalent"):
        for kw in _CATEGORY_KEYWORDS[cat]:
            if kw in lower:
                return cat
    for cat in ("electronics", "apparel"):
        for kw in _CATEGORY_KEYWORDS[cat]:
            if kw in lower:
                return cat
    return "general"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def build_discovery_record(
    original_url: str,
    final_url: str,
    raw_html: str,
    soup: Optional[BeautifulSoup] = None,
    extracted_title: str = "",
    extracted_price: float = 0.0,
) -> DiscoveryRecord:
    """Run the full sanitization pipeline and return a verified record."""
    if soup is None:
        soup = BeautifulSoup(raw_html, "html.parser")

    # Title sanitization
    clean_title, title_injected, title_hits = sanitize_title(extracted_title)

    # Hidden-element injection scan
    page_injected, page_hits = detect_injections(soup)

    injection_detected = title_injected or page_injected
    injection_details = title_hits + page_hits

    # Multi-signal extraction and consistency
    signals = extract_all_signals(soup)
    consistency = score_consistency(signals)

    # Category classification
    category = classify_category(clean_title)

    # Page fingerprint (for TOCTOU detection later)
    fingerprint = hashlib.sha256(raw_html.encode()).hexdigest()

    return DiscoveryRecord(
        title=clean_title,
        price_sgd=extracted_price,
        final_url=final_url,
        original_url=original_url,
        product_category=category,
        injection_detected=injection_detected,
        injection_details=injection_details,
        consistency_score=consistency,
        page_fingerprint=fingerprint,
        signals=signals,
    )
