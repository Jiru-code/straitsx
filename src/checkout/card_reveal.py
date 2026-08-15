"""Reveals card details from the StraitsX-hosted card_html iframe.

The sandbox `get_card_sandbox` flow does NOT return a raw PAN/CVV/expiry
-- it returns `card_html`, a one-time iframe that presumably renders those
fields for a human (or an automated agent) to read. This module tries to
extract them via Playwright so the checkout step can still autofill a form.

CAVEAT: the actual DOM structure of that iframe is unknown -- this was
written without being able to reach card.straitsx.ai to inspect it. The
selectors below (`data-testid="card-number"` etc.) are placeholders /
best guesses at common patterns for hosted-card-field providers. Run:

    python -m src.checkout.inspect_card_html <card_html_url>

against a real sandbox card once you have one, look at the printed page
text/HTML, and update SELECTORS below to match.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import sync_playwright

from src.config import settings

# Best-guess selectors -- update after inspecting a real card_html page.
SELECTORS = {
    "pan": '[data-testid="card-number"], .card-number, #card-number',
    "expiry": '[data-testid="card-expiry"], .card-expiry, #card-expiry',
    "cvv": '[data-testid="card-cvv"], .card-cvv, #card-cvv',
}


@dataclass
class RevealedCard:
    pan: Optional[str]
    expiry: Optional[str]
    cvv: Optional[str]
    raw_text: str  # full page text, for debugging when selectors don't match


def reveal_card(card_html_url: str) -> RevealedCard:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=settings.checkout_headless)
        page = browser.new_page()
        page.goto(card_html_url)
        page.wait_for_load_state("networkidle")

        def _text(selector: str) -> Optional[str]:
            try:
                el = page.query_selector(selector)
                return el.inner_text().strip() if el else None
            except Exception:
                return None

        pan = _text(SELECTORS["pan"])
        expiry = _text(SELECTORS["expiry"])
        cvv = _text(SELECTORS["cvv"])
        raw_text = page.inner_text("body")

        browser.close()
        return RevealedCard(pan=pan, expiry=expiry, cvv=cvv, raw_text=raw_text)
