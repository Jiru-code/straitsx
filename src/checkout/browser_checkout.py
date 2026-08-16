"""Checkout automation: fills in virtual card details on a product page and
submits the payment form. Runs against the local demo shop fixture by
default; point CHECKOUT at a real sandbox merchant to test against something
live.

On Streamlit Cloud (or any environment without Playwright browsers
installed) the checkout is simulated so the rest of the demo still works.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from src.config import settings
from src.straitsx.client import VirtualCard


@dataclass
class CheckoutReceipt:
    success: bool
    detail: str


def _is_streamlit_cloud() -> bool:
    """Detect the Streamlit Cloud runtime (sets HOME=/home/adminuser)."""
    return os.getenv("HOME", "").startswith("/home/adminuser")


def _resolve_url(url: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("file://"):
        # Resolve relative file:// paths to absolute
        raw_path = url.replace("file://", "")
        return f"file://{Path(raw_path).resolve()}"
    path = Path(url).resolve()
    return f"file://{path}"


def _simulate_checkout(product_url: str, card: VirtualCard) -> CheckoutReceipt:
    """Return a simulated successful receipt (no browser required)."""
    masked_pan = f"****-****-****-{card.pan[-4:]}" if len(card.pan) >= 4 else card.pan
    return CheckoutReceipt(
        success=True,
        detail=(
            f"[simulated] Payment of ${card.amount_sgd:.2f} SGD "
            f"with card {masked_pan} completed for {product_url}"
        ),
    )


def run_checkout(product_url: str, card: VirtualCard) -> CheckoutReceipt:
    # On Streamlit Cloud Playwright browsers aren't available — simulate.
    if _is_streamlit_cloud():
        return _simulate_checkout(product_url, card)

    from playwright.sync_api import sync_playwright

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
