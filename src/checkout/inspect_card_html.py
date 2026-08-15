"""Inspect a real card_html iframe URL once you have one, so you can update
the selectors in card_reveal.py to match the actual DOM.

Usage:
    python -m src.checkout.inspect_card_html "https://card.straitsx.ai/view/..."
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m src.checkout.inspect_card_html <card_html_url>")
        sys.exit(1)

    url = sys.argv[1]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url)
        page.wait_for_load_state("networkidle")
        print("=== Page text ===")
        print(page.inner_text("body"))
        print("\n=== Full HTML ===")
        print(page.content())
        browser.close()


if __name__ == "__main__":
    main()
