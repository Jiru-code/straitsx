from src.policy.spending_policy import SpendingPolicy


def test_approves_within_limits():
    p = SpendingPolicy(max_transaction_sgd=50, daily_limit_sgd=200, allowed_domains=["demo-shop.test"])
    decision = p.evaluate(30, "https://demo-shop.test/item")
    assert decision.approved


def test_blocks_over_per_transaction_cap():
    p = SpendingPolicy(max_transaction_sgd=50, daily_limit_sgd=200, allowed_domains=["demo-shop.test"])
    decision = p.evaluate(75, "https://demo-shop.test/item")
    assert not decision.approved
    assert "per-transaction" in decision.reason


def test_blocks_disallowed_merchant():
    p = SpendingPolicy(max_transaction_sgd=50, daily_limit_sgd=200, allowed_domains=["demo-shop.test"])
    decision = p.evaluate(10, "https://sketchy-site.example/item")
    assert not decision.approved
    assert "allow-list" in decision.reason


def test_blocks_over_daily_limit_across_calls():
    p = SpendingPolicy(max_transaction_sgd=50, daily_limit_sgd=60, allowed_domains=["demo-shop.test"])
    first = p.evaluate(40, "https://demo-shop.test/item")
    assert first.approved
    p.record_spend(40)
    second = p.evaluate(30, "https://demo-shop.test/item")
    assert not second.approved
    assert "daily limit" in second.reason
