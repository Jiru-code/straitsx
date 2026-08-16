"""Checkout automation: fills in virtual card details on a product page and
submits the payment form. Runs against the local demo shop fixture by
default; point CHECKOUT at a real sandbox merchant to test against something
live.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright

from src.config import settings
from src.straitsx.client import VirtualCard


@dataclass
class CheckoutReceipt:
    success: bool
    detail: str


def _resolve_url(url: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("file://"):
        # Resolve relative file:// paths to absolute
        raw_path = url.replace("file://", "")
        return f"file://{Path(raw_path).resolve()}"
    path = Path(url).resolve()
    return f"file://{path}"


def run_checkout(product_url: str, card: VirtualCard) -> CheckoutReceipt:
    url = _resolve_url(product_url)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=settings.checkout_headless)
        page = browser.new_page()
        page.goto(url)

        try:
            page.fill("#card-number", card.pan)
            page.fill("#card-expiry", card.expiry)
            page.fill("#card-cvv", card.cvv)
            page.click("#pay-button")
            page.wait_for_selector("#receipt", state="visible", timeout=5000)
            receipt_text = page.inner_text("#receipt")
            browser.close()
            return CheckoutReceipt(success=True, detail=receipt_text)
        except Exception as exc:  # narrow demo scope: surface any failure as a failed checkout
            browser.close()
            return CheckoutReceipt(success=False, detail=f"Checkout failed: {exc}")
