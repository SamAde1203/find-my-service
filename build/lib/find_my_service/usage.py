"""Tracks AI token use and cost, and enforces a daily budget."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, date, datetime


@dataclass
class UsageTracker:
    price_input_per_mtok: float
    price_output_per_mtok: float
    daily_budget_usd: float
    day: date = field(default_factory=lambda: datetime.now(UTC).date())
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def cost_usd(self) -> float:
        return (
            self.input_tokens * self.price_input_per_mtok
            + self.output_tokens * self.price_output_per_mtok
        ) / 1_000_000

    def _roll_over(self) -> None:
        today = datetime.now(UTC).date()
        if today != self.day:
            self.day, self.input_tokens, self.output_tokens, self.calls = today, 0, 0, 0

    def can_spend(self) -> bool:
        with self._lock:
            self._roll_over()
            return self.cost_usd < self.daily_budget_usd

    def record(self, input_tokens: int, output_tokens: int) -> float:
        """Adds one AI call and returns its cost in US dollars."""
        with self._lock:
            self._roll_over()
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            self.calls += 1
            return (
                input_tokens * self.price_input_per_mtok
                + output_tokens * self.price_output_per_mtok
            ) / 1_000_000

    def snapshot(self) -> dict[str, float | int | str]:
        with self._lock:
            self._roll_over()
            return {
                "day": self.day.isoformat(),
                "calls": self.calls,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cost_usd": round(self.cost_usd, 6),
                "daily_budget_usd": self.daily_budget_usd,
            }
