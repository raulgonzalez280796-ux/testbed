"""Batch token budget: stop launching trials at the cap, stop running ones at cap + tolerance."""

from __future__ import annotations

import statistics
import threading

from .engine import TrialStopped
from .providers.base import Usage


class BatchBudget:
    def __init__(self, max_total_tokens: int | None, tolerance: float = 0.10, used: int = 0):
        self.cap = max_total_tokens
        self.tolerance = tolerance
        self.used = used
        self.trial_totals: list[int] = []
        self.lock = threading.Lock()

    @property
    def hard_cap(self) -> float | None:
        return None if self.cap is None else self.cap * (1 + self.tolerance)

    def charge(self, usage: Usage) -> None:
        with self.lock:
            self.used += usage.total
            over = self.hard_cap is not None and self.used >= self.hard_cap
        if over:
            raise TrialStopped("budget_stopped", "budget_stopped")

    def precheck(self, estimate: int) -> None:
        """Before a model call: stop if it would likely push spending past cap + tolerance."""
        with self.lock:
            over = self.hard_cap is not None and self.used + estimate > self.hard_cap
        if over:
            raise TrialStopped("budget_stopped", "budget_stopped")

    def record_trial(self, tokens: int) -> None:
        with self.lock:
            self.trial_totals.append(tokens)

    def per_trial_estimate(self) -> float | None:
        if not self.trial_totals:
            return None
        return statistics.median(self.trial_totals)

    def can_launch(self, running: int) -> bool:
        """False once tokens used plus the expected cost of running trials reach the cap."""
        if self.cap is None:
            return True
        estimate = self.per_trial_estimate() or 0
        return self.used + running * estimate < self.cap


def projection(trial_totals: list[int], trials: int) -> dict:
    """Projected batch tokens from pilot trials (median and p90 times the trial count)."""
    if not trial_totals:
        return {}
    ordered = sorted(trial_totals)
    p90 = ordered[min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))]
    median = statistics.median(ordered)
    return {
        "pilot_trials": len(ordered), "median_per_trial": median, "p90_per_trial": p90,
        "projected_median": round(median * trials), "projected_p90": round(p90 * trials),
    }
