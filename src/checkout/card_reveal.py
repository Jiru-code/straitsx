"""Reveals card details from the StraitsX card_html response.

The sandbox cardapi returns `card_html` — raw HTML containing the card
number, expiry, and CVV in a styled card layout. This module extracts
those fields so the checkout step can autofill a payment form.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup


@dataclass
class RevealedCard:
    pan: Optional[str]
    expiry: Optional[str]
    cvv: Optional[str]
    raw_text: str


def reveal_card(card_html: str) -> RevealedCard:
    soup = BeautifulSoup(card_html, "html.parser")
    raw_text = soup.get_text(" ", strip=True)

    def _text(selector: str) -> Optional[str]:
        el = soup.select_one(selector)
        return el.get_text(strip=True) if el else None

    pan = _text(".card-number")
    expiry = _text(".exp_val")
    cvv = _text(".cvv_val")

    return RevealedCard(pan=pan, expiry=expiry, cvv=cvv, raw_text=raw_text)
