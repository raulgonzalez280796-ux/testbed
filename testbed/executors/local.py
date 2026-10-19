"""Run a batch on this machine: up to N trials at once as asyncio tasks.

Trials spend nearly all their time waiting for the API, so one process holds
many of them (one process per trial would cost ~80 MB each).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from ..batch import Summary, load_batch
from ..budget import BatchBudget
from ..keys import load_keys, missing_keys
from ..settings import load_settings
from .ratelimit import RateLimiter
from ..trial import BatchContext, is_complete, now, run_trial

BREAKER_THRESHOLD = 5  # consecutive trials failing with the same infrastructure error


class MissingKeys(Exception):
    def __init__(self, names: list[str]):
        super().__init__("Missing API keys: " + ", ".join(names))
        self.names = names


class CircuitBreaker:
    def __init__(self, threshold: int = BREAKER_THRESHOLD):
        self.threshold = threshold
        self.last: str | None = None
        self.count = 0
        self.tripped: str | None = None

    def record(self, result: dict) -> None:
        if result.get("status") != "infra_error":
            self.last, self.count = None, 0
            return
        error = result.get("error") or {}
        signature = f"{error.get('kind')}:{error.get('type')}:{error.get('status_code')}"
        self.count = self.count + 1 if signature == self.last else 1
        self.last = signature
        if self.count >= self.threshold and self.tripped is None:
            self.tripped = (
                f"Stopped after {self.count} trials in a row failed with the same error "
                f"({signature}): {error.get('message', '')[:300]}"
            )


async def run_batch_dir(
    batch_dir: Path,
    home: Path,
    *,
    parallel: int | None = None,
    keys: dict[str, str] | None = None,
    indices: list[int] | None = None,
    limiter=None,
) -> dict:
    spec, scenario = load_batch(batch_dir)
    keys = load_keys(home) if keys is None else keys
    missing = missing_keys(scenario.providers, keys)
    if missing:
        raise MissingKeys(missing)
    parallel = max(1, parallel or spec["parallel"])
    if limiter is None:
        limiter = RateLimiter(load_settings(home))
    summary = Summary(batch_dir, spec, scenario)
    summary.status, summary.started_at = "running", now()
    cancel_file = batch_dir / "cancel"
    cancel_file.unlink(missing_ok=True)

    used = sum(row["tokens"] or 0 for row in summary.rows.values())
    budget = BatchBudget(scenario.budget.max_total_tokens, scenario.budget.overrun_tolerance, used)
    for row in summary.rows.values():
        if row["status"] == "completed" and row["tokens"]:
            budget.record_trial(row["tokens"])
    breaker = CircuitBreaker()
    stop = {"reason": None}

    def cancelled() -> bool:
        return stop["reason"] == "cancelled" or cancel_file.exists()

    batch = BatchContext(
        batch_dir=batch_dir, batch_id=spec["batch_id"], scenario=scenario,
        seed=spec["seed"], keys=keys, charge=budget.charge, precheck=budget.precheck,
        cancelled=cancelled,
        limiter=limiter,
    )
    todo = [
        i for i in (indices or range(1, spec["trials"] + 1))
        if not is_complete(batch.trial_dir(i))
    ]
    for index in todo:
        summary.set(index, status="queued")
    dirty = asyncio.Event()
    running: set[asyncio.Task] = set()

    async def saver():
        while True:
            await dirty.wait()
            dirty.clear()
            summary.running_peak = max(summary.running_peak, len(running))
            summary.rate_limits = limiter.snapshot()
            summary.save()
            await asyncio.sleep(0.5)

    async def one(index: int):
        summary.set(index, status="running")
        dirty.set()
        result = await run_trial(batch, index)
        summary.refresh(index)
        budget.record_trial((result.get("tokens") or {}).get("total", 0))
        breaker.record(result)
        dirty.set()

    summary.save()
    saver_task = asyncio.create_task(saver())
    slots = asyncio.Semaphore(parallel)
    started = time.monotonic()
    try:
        for position, index in enumerate(todo):
            await slots.acquire()
            reason = (
                "cancelled" if cancelled()
                else "budget_stopped" if not budget.can_launch(len(running))
                else "circuit_breaker" if breaker.tripped
                else None
            )
            if reason:
                slots.release()
                stop["reason"] = stop["reason"] or reason
                for rest in todo[position:]:
                    summary.set(rest, status="cancelled" if reason == "cancelled" else "not_run")
                break
            task = asyncio.create_task(one(index))
            running.add(task)
            task.add_done_callback(lambda t: (running.discard(t), slots.release()))
        if running:
            await asyncio.gather(*running, return_exceptions=True)
    except asyncio.CancelledError:
        stop["reason"] = "cancelled"
        for task in list(running):
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        raise
    finally:
        saver_task.cancel()
        await batch.adapters.close()
        if cancelled():
            stop["reason"] = "cancelled"
        summary.status = {
            None: "finished", "cancelled": "cancelled",
            "budget_stopped": "budget_stopped", "circuit_breaker": "stopped",
        }[stop["reason"]]
        summary.message = breaker.tripped if stop["reason"] == "circuit_breaker" else (
            f"Token cap reached ({budget.used:,} tokens used)" if stop["reason"] == "budget_stopped" else None
        )
        summary.finished_at = now()
        summary.rate_limits = limiter.snapshot()
        for index in summary.rows:
            if summary.rows[index]["status"] in ("running", "queued", "incomplete"):
                summary.refresh(index)
        summary.save()
    summary_data = {
        "batch_dir": str(batch_dir), "status": summary.status, "message": summary.message,
        "counts": summary.counts(), "tokens_total": budget.used,
        "seconds": round(time.monotonic() - started, 1),
        "running_peak": summary.running_peak, "rate_limits": summary.rate_limits,
    }
    return summary_data
