"""Reveals card details from the StraitsX card_html response.

The sandbox cardapi returns ``card_html`` — raw HTML containing the card
number, expiry, and CVV in a styled card layout.  This module extracts
those fields so the checkout step can autofill a payment form.

Multiple CSS selector sets are tried in priority order, with a regex
fallback on the raw text as a last resort.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup


@dataclass
class RevealedCard:
    pan: Optional[str]
    expiry: Optional[str]
    cvv: Optional[str]
    raw_text: str


# Multiple selector sets, tried in order (the real DOM structure isn't
# confirmed — update these once you can inspect a live card_html response).
PAN_SELECTORS = [".card-number", ".pan", "[data-card-number]", "#cardNumber", "#card-number"]
EXPIRY_SELECTORS = [".exp_val", ".expiry", "[data-expiry]", "#expiry", ".card-expiry"]
CVV_SELECTORS = [".cvv_val", ".cvv", "[data-cvv]", "#cvv", ".card-cvv"]


def _try_selectors(soup: BeautifulSoup, selectors: list[str]) -> Optional[str]:
    """Try each CSS selector in order; return the first match's text."""
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(strip=True)
            if text:
                return text
    return None


def _regex_pan(text: str) -> Optional[str]:
    """Extract a 16-digit PAN from raw text."""
    # Match 16 digits, optionally separated by spaces or dashes
    m = re.search(r"\b(\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4})\b", text)
    if m:
        return re.sub(r"[\s-]", "", m.group(1))
    return None


def _regex_expiry(text: str) -> Optional[str]:
    """Extract MM/YY expiry from raw text."""
    m = re.search(r"\b(0[1-9]|1[0-2])\s*/\s*(\d{2,4})\b", text)
    if m:
        month = m.group(1)
        year = m.group(2)
        if len(year) == 4:
            year = year[-2:]
        return f"{month}/{year}"
    return None


def _regex_cvv(text: str) -> Optional[str]:
    """Extract a 3-4 digit CVV from raw text (heuristic — picks the first
    isolated 3-4 digit group that isn't part of a longer number)."""
    m = re.search(r"(?<!\d)(\d{3,4})(?!\d)", text)
    if m:
        return m.group(1)
    return None


def reveal_card(card_html: str) -> RevealedCard:
    """Extract PAN, expiry, and CVV from a card_html string.

    Tries CSS selectors first, then falls back to regex on raw text.
    """
    soup = BeautifulSoup(card_html, "html.parser")
    raw_text = soup.get_text(" ", strip=True)

    pan = _try_selectors(soup, PAN_SELECTORS) or _regex_pan(raw_text)
    expiry = _try_selectors(soup, EXPIRY_SELECTORS) or _regex_expiry(raw_text)
    cvv = _try_selectors(soup, CVV_SELECTORS) or _regex_cvv(raw_text)

    return RevealedCard(pan=pan, expiry=expiry, cvv=cvv, raw_text=raw_text)
