"""Spend-safety policy engine.

This is the gatekeeper: it runs as its own graph node *before* any XSGD is
redeemed or any card is issued, so the agent's spending is bounded by config
rather than by what the LLM decides to do. Rules are intentionally simple
and auditable rather than clever.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlparse

from src.config import settings


@dataclass
class PolicyDecision:
    approved: bool
    reason: str


@dataclass
class SpendingPolicy:
    max_transaction_sgd: float = field(default_factory=lambda: settings.max_transaction_sgd)
    daily_limit_sgd: float = field(default_factory=lambda: settings.daily_limit_sgd)
    allowed_domains: list[str] = field(default_factory=lambda: settings.allowed_merchant_domains)

    # in-memory spend tracker; swap for a persisted store (DB/file) in production
    _spent_today: float = 0.0
    _tracked_date: date = field(default_factory=date.today)

    def _reset_if_new_day(self) -> None:
        today = date.today()
        if today != self._tracked_date:
            self._tracked_date = today
            self._spent_today = 0.0

    def evaluate(self, amount_sgd: float, merchant_url: str) -> PolicyDecision:
        self._reset_if_new_day()

        parsed = urlparse(merchant_url)
        if parsed.scheme == "file":
            # local demo fixtures have no host; treat them as "localhost"
            domain = "localhost"
        else:
            domain = (parsed.netloc or merchant_url).split(":")[0]

        if amount_sgd <= 0:
            return PolicyDecision(False, "Amount must be positive.")

        if amount_sgd > self.max_transaction_sgd:
            return PolicyDecision(
                False,
                f"Amount {amount_sgd:.2f} SGD exceeds per-transaction cap of "
                f"{self.max_transaction_sgd:.2f} SGD.",
            )

        if self._spent_today + amount_sgd > self.daily_limit_sgd:
            return PolicyDecision(
                False,
                f"Amount {amount_sgd:.2f} SGD would exceed daily limit "
                f"({self._spent_today:.2f}/{self.daily_limit_sgd:.2f} SGD already spent today).",
            )

        if self.allowed_domains and domain not in self.allowed_domains:
            return PolicyDecision(
                False,
                f"Merchant domain '{domain}' is not on the allow-list "
                f"({', '.join(self.allowed_domains)}).",
            )

        return PolicyDecision(True, "Within per-transaction cap, daily limit, and merchant allow-list.")

    def record_spend(self, amount_sgd: float) -> None:
        self._reset_if_new_day()
        self._spent_today += amount_sgd


# Module-level singleton so state persists across tool calls within a run.
policy = SpendingPolicy()
