"""Before a big batch: project its token use from earlier trials of the same scenario.

Batches above PILOT_THRESHOLD trials need a projection and a confirmation.
The projection uses completed trials from earlier batches with the same
scenario hash; if there are fewer than PILOT_TRIALS, a small pilot batch runs first.
"""

from __future__ import annotations

import json
from pathlib import Path

from .batch import list_batches
from .budget import projection
from .scenario import Scenario, load_scenario, scenario_hash

PILOT_THRESHOLD = 20
PILOT_TRIALS = 3


class ConfirmationRequired(Exception):
    def __init__(self, plan: dict):
        super().__init__(describe(plan))
        self.plan = plan


def previous_totals(home: Path, digest: str) -> list[int]:
    totals = []
    for batch_dir in list_batches(home):
        try:
            spec = json.loads((batch_dir / "batch.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if spec.get("scenario_hash") != digest:
            continue
        for result_path in batch_dir.glob("trial_*/result.json"):
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if result.get("status") == "completed":
                totals.append((result.get("tokens") or {}).get("total", 0))
    return totals


def plan_batch(home: Path, scenario: Scenario, trials: int) -> dict:
    digest = scenario_hash(scenario)
    totals = previous_totals(home, digest)
    plan = {
        "trials": trials, "scenario_hash": digest,
        "needs_confirmation": trials > PILOT_THRESHOLD,
        "needs_pilot": trials > PILOT_THRESHOLD and len(totals) < PILOT_TRIALS,
        "projection": projection(totals, trials) if totals else None,
        "warnings": [],
    }
    cap = scenario.budget.max_total_tokens
    if plan["projection"] and cap and plan["projection"]["projected_median"] > cap:
        plan["warnings"].append(
            f"The projected total ({plan['projection']['projected_median']:,} tokens) is above the "
            f"batch cap ({cap:,}); the batch will stop early."
        )
    if cap is None and plan["needs_confirmation"]:
        plan["warnings"].append("The scenario has no batch token cap (budget.max_total_tokens).")
    return plan


def describe(plan: dict) -> str:
    projected = plan.get("projection")
    if not projected:
        return f"{plan['trials']} trials: no projection yet (a pilot is needed)."
    lines = [
        f"{plan['trials']} trials: about {projected['projected_median']:,} tokens "
        f"(p90 {projected['projected_p90']:,}), from {projected['pilot_trials']} earlier trials "
        f"at {projected['median_per_trial']:,.0f} tokens each (median).",
    ]
    lines += plan.get("warnings", [])
    return "\n".join(lines)


def plan_for_path(home: Path, scenario_path: Path, trials: int) -> dict:
    return plan_batch(home, load_scenario(scenario_path), trials)
