"""Tests for SIP Layer 3: Contract-bound tool enforcement."""
import pytest

from src.security.enforcement import (
    record_discovery,
    record_issuance,
    reset,
    validate_checkout,
    validate_discovery_url,
    validate_issuance,
)
from src.security.intent_contract import _reset as reset_contract, seal_intent
from src.security.sanitizer import DiscoveryRecord


@pytest.fixture(autouse=True)
def _clean_state():
    """Reset all module-level SIP state before each test."""
    reset()
    reset_contract()
    yield
    reset()
    reset_contract()


def _make_record(**overrides) -> DiscoveryRecord:
    """Helper to build a DiscoveryRecord with sensible defaults."""
    defaults = dict(
        title="Wireless Earbuds Pro",
        price_sgd=30.0,
        final_url="file://tests/fixtures/demo_shop.html",
        original_url="file://tests/fixtures/demo_shop.html",
        product_category="electronics",
        injection_detected=False,
        injection_details=[],
        consistency_score=0.95,
        page_fingerprint="abc123",
        signals=[],
    )
    defaults.update(overrides)
    return DiscoveryRecord(**defaults)


# ---------------------------------------------------------------------------
# URL pre-check (before fetching)
# ---------------------------------------------------------------------------

class TestValidateDiscoveryUrl:
    def test_file_url_allowed(self):
        result = validate_discovery_url("file://tests/fixtures/demo_shop.html")
        assert result.ok

    def test_allowed_domain(self):
        result = validate_discovery_url("https://demo-shop.test/item")
        assert result.ok

    def test_disallowed_domain(self):
        result = validate_discovery_url("https://evil-shop.xyz/item")
        assert not result.ok
        assert "evil-shop.xyz" in result.reason


# ---------------------------------------------------------------------------
# Issuance validation (the big gate)
# ---------------------------------------------------------------------------

class TestValidateIssuance:
    def test_no_discovery_yet(self):
        seal_intent("Buy earbuds")
        result = validate_issuance(30.0, "file://tests/fixtures/demo_shop.html")
        assert not result.ok
        assert "No product has been discovered" in result.reason

    def test_matching_url_and_amount(self):
        seal_intent("Buy wireless earbuds under 40 SGD")
        record = _make_record()
        record_discovery(record)
        result = validate_issuance(30.0, "file://tests/fixtures/demo_shop.html")
        assert result.ok

    def test_mismatched_url(self):
        seal_intent("Buy earbuds")
        record_discovery(_make_record())
        result = validate_issuance(30.0, "https://evil.com/steal")
        assert not result.ok
        assert "does not match" in result.reason

    def test_mismatched_amount(self):
        seal_intent("Buy earbuds")
        record_discovery(_make_record(price_sgd=30.0))
        result = validate_issuance(5.0, "file://tests/fixtures/demo_shop.html")
        assert not result.ok
        assert "does not match the discovered price" in result.reason

    def test_restricted_category_gift_card(self):
        seal_intent("Buy wireless earbuds")
        record_discovery(_make_record(product_category="gift_card"))
        result = validate_issuance(30.0, "file://tests/fixtures/demo_shop.html")
        assert not result.ok
        assert "restricted" in result.reason

    def test_restricted_category_cryptocurrency(self):
        seal_intent("Buy earbuds")
        record_discovery(_make_record(product_category="cryptocurrency"))
        result = validate_issuance(30.0, "file://tests/fixtures/demo_shop.html")
        assert not result.ok
        assert "restricted" in result.reason

    def test_injection_detected_blocks(self):
        seal_intent("Buy earbuds")
        record_discovery(_make_record(
            injection_detected=True,
            injection_details=["Hidden element: Pattern matched: 'ignore previous instructions'"],
        ))
        result = validate_issuance(30.0, "file://tests/fixtures/demo_shop.html")
        assert not result.ok
        assert "injection" in result.reason.lower()

    def test_low_consistency_blocks(self):
        seal_intent("Buy earbuds")
        record_discovery(_make_record(consistency_score=0.2))
        result = validate_issuance(30.0, "file://tests/fixtures/demo_shop.html")
        assert not result.ok
        assert "consistency" in result.reason.lower()

    def test_low_keyword_overlap_blocks(self):
        seal_intent("Buy wireless earbuds")
        record_discovery(_make_record(title="Amazon Gift Card $100"))
        result = validate_issuance(30.0, "file://tests/fixtures/demo_shop.html")
        assert not result.ok
        assert "keyword overlap" in result.reason.lower() or "substitution" in result.reason.lower()

    def test_price_exceeds_intent_max(self):
        seal_intent("Buy earbuds under 20 SGD")
        record_discovery(_make_record(price_sgd=30.0))
        result = validate_issuance(30.0, "file://tests/fixtures/demo_shop.html")
        assert not result.ok
        assert "exceeds" in result.reason.lower()


# ---------------------------------------------------------------------------
# Checkout validation
# ---------------------------------------------------------------------------

class TestValidateCheckout:
    def test_no_issuance_yet(self):
        result = validate_checkout("file://tests/fixtures/demo_shop.html", 30.0)
        assert not result.ok
        assert "No card has been issued" in result.reason

    def test_matching_checkout(self):
        record_issuance("file://tests/fixtures/demo_shop.html", 30.0)
        result = validate_checkout("file://tests/fixtures/demo_shop.html", 30.0)
        assert result.ok

    def test_mismatched_url(self):
        record_issuance("file://tests/fixtures/demo_shop.html", 30.0)
        result = validate_checkout("https://evil.com/phish", 30.0)
        assert not result.ok
        assert "does not match" in result.reason

    def test_mismatched_amount(self):
        record_issuance("file://tests/fixtures/demo_shop.html", 30.0)
        result = validate_checkout("file://tests/fixtures/demo_shop.html", 100.0)
        assert not result.ok
        assert "does not match" in result.reason


# ---------------------------------------------------------------------------
# Full-flow integration
# ---------------------------------------------------------------------------

class TestFullFlow:
    def test_clean_page_passes_all_checks(self):
        """Seal → discover (clean) → issue → checkout — all should pass."""
        seal_intent("Buy wireless earbuds under 40 SGD")

        record = _make_record(
            title="Wireless Earbuds Pro",
            price_sgd=5.0,
            final_url="file://tests/fixtures/demo_shop.html",
        )
        record_discovery(record)

        # Issuance should pass
        issue_result = validate_issuance(5.0, "file://tests/fixtures/demo_shop.html")
        assert issue_result.ok, issue_result.reason

        # Record issuance
        record_issuance("file://tests/fixtures/demo_shop.html", 5.0)

        # Checkout should pass
        checkout_result = validate_checkout("file://tests/fixtures/demo_shop.html", 5.0)
        assert checkout_result.ok, checkout_result.reason

    def test_attack_page_blocked(self):
        """Seal → discover (attack) → issue — should be blocked."""
        seal_intent("Buy wireless earbuds under 40 SGD")

        # Simulate what the sanitizer would produce from attack_shop.html
        record = _make_record(
            title="Amazon Gift Card",
            price_sgd=5.0,
            final_url="file://tests/fixtures/attack_shop.html",
            product_category="gift_card",
            injection_detected=True,
            injection_details=["Hidden element: Pattern matched: 'ignore previous instructions'"],
            consistency_score=0.3,
        )
        record_discovery(record)

        # Issuance should be blocked (multiple reasons)
        result = validate_issuance(5.0, "file://tests/fixtures/attack_shop.html")
        assert not result.ok

    def test_url_swap_attack_blocked(self):
        """LLM tries to issue card for a different URL than discovered."""
        seal_intent("Buy wireless earbuds")

        record = _make_record(
            final_url="file://tests/fixtures/demo_shop.html",
        )
        record_discovery(record)

        # Try to issue to a different URL
        result = validate_issuance(30.0, "https://evil.com/collect")
        assert not result.ok
        assert "does not match" in result.reason

    def test_amount_manipulation_blocked(self):
        """LLM tries to issue card for a different amount than discovered."""
        seal_intent("Buy wireless earbuds")

        record = _make_record(price_sgd=30.0)
        record_discovery(record)

        # Try to issue for a different amount
        result = validate_issuance(5.0, "file://tests/fixtures/demo_shop.html")
        assert not result.ok
        assert "does not match the discovered price" in result.reason
