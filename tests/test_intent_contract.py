"""Tests for SIP Layer 1: Intent Contract sealing and parsing."""
from src.security.intent_contract import (
    IntentContract,
    _extract_keywords,
    _extract_max_price,
    _extract_quantity,
    _extract_url,
    _reset,
    get_contract,
    seal_intent,
)


class TestExtractKeywords:
    def test_basic(self):
        kw = _extract_keywords("Buy wireless earbuds under 40 SGD")
        assert "wireless" in kw
        assert "earbuds" in kw

    def test_strips_stopwords(self):
        kw = _extract_keywords("Buy me the best wireless earbuds please")
        assert "me" not in kw
        assert "the" not in kw
        assert "buy" not in kw
        assert "wireless" in kw

    def test_strips_urls(self):
        kw = _extract_keywords("Buy from https://shop.com/earbuds")
        assert "https" not in kw
        assert "shop" not in kw
        assert "com" not in kw

    def test_empty_instruction(self):
        kw = _extract_keywords("")
        assert kw == []


class TestExtractMaxPrice:
    def test_under_sgd(self):
        assert _extract_max_price("Buy earbuds under 40 SGD") == 40.0

    def test_below_dollar(self):
        assert _extract_max_price("below $50") == 50.0

    def test_less_than(self):
        assert _extract_max_price("less than 25 SGD") == 25.0

    def test_max(self):
        assert _extract_max_price("max 30 SGD") == 30.0

    def test_budget(self):
        assert _extract_max_price("budget of 100 SGD") == 100.0

    def test_no_price(self):
        assert _extract_max_price("Buy wireless earbuds") is None


class TestExtractQuantity:
    def test_default_is_one(self):
        assert _extract_quantity("Buy wireless earbuds") == 1

    def test_explicit_quantity(self):
        assert _extract_quantity("Buy 3 pieces of earbuds") == 3

    def test_items(self):
        assert _extract_quantity("Buy 2 items from the shop") == 2

    def test_units(self):
        assert _extract_quantity("Order 5 units") == 5


class TestExtractUrl:
    def test_https(self):
        assert _extract_url("Buy from https://shop.com/item") == "https://shop.com/item"

    def test_file(self):
        assert _extract_url("Buy at file:///tmp/shop.html") == "file:///tmp/shop.html"

    def test_no_url(self):
        assert _extract_url("Buy wireless earbuds") is None


class TestSealIntent:
    def setup_method(self):
        _reset()

    def test_creates_contract(self):
        contract = seal_intent("Buy wireless earbuds under 40 SGD")
        assert isinstance(contract, IntentContract)
        assert "wireless" in contract.product_keywords
        assert "earbuds" in contract.product_keywords
        assert contract.max_price_sgd == 40.0
        assert contract.quantity == 1
        assert contract.merchant_url is None

    def test_stored_as_singleton(self):
        seal_intent("Buy wireless earbuds")
        contract = get_contract()
        assert contract is not None
        assert "wireless" in contract.product_keywords

    def test_reseal_replaces(self):
        seal_intent("Buy earbuds")
        seal_intent("Buy a charger")
        contract = get_contract()
        assert "charger" in contract.product_keywords

    def test_hash_deterministic(self):
        c1 = seal_intent("Buy wireless earbuds under 40 SGD")
        _reset()
        c2 = seal_intent("Buy wireless earbuds under 40 SGD")
        assert c1.contract_hash == c2.contract_hash

    def test_restricted_categories(self):
        contract = seal_intent("Buy something")
        assert "gift card" in contract.restricted_categories
        assert "cryptocurrency" in contract.restricted_categories
        assert "bitcoin" in contract.restricted_categories

    def test_with_url(self):
        contract = seal_intent("Buy from https://shop.com/item under 30 SGD")
        assert contract.merchant_url == "https://shop.com/item"
        assert contract.max_price_sgd == 30.0
