"""Per-provider rate limiting shared by every trial in a process.

- Token buckets for requests/minute and input tokens/minute (if configured).
- A cap on requests in flight that halves on a 429 and grows back slowly,
  so a batch settles at the rate the account really allows.
- After a 429 with retry-after, new requests to that provider wait it out.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from ..settings import ProviderLimits, Settings


def estimate_input_tokens(request: dict) -> int:
    """Rough size of a request (about 4 characters per token); good enough for pacing."""
    try:
        return max(1, len(json.dumps(request, ensure_ascii=False, default=str)) // 4)
    except (TypeError, ValueError):
        return 1


class TokenBucket:
    def __init__(self, per_minute: float):
        self.rate = per_minute / 60.0
        self.capacity = per_minute
        self.level = per_minute
        self.updated = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        self.level = min(self.capacity, self.level + (now - self.updated) * self.rate)
        self.updated = now

    def wait_time(self, amount: float) -> float:
        self._refill()
        amount = min(amount, self.capacity)  # one huge request still gets through eventually
        return 0.0 if self.level >= amount else (amount - self.level) / self.rate

    def take(self, amount: float) -> None:
        self._refill()
        self.level -= min(amount, self.capacity)


DECREASE = 0.7        # on a 429: multiply the in-flight cap by this...
COOLDOWN_SECONDS = 2.0  # ...at most once per this long (a burst of 429s is one signal)
INCREASE = 0.05       # on each success: grow the cap back by this much


@dataclass
class ProviderState:
    limits: ProviderLimits
    allowed: float = 0.0
    in_flight: int = 0
    paused_until: float = 0.0
    last_decrease: float = -1e9
    requests: TokenBucket | None = None
    tokens: TokenBucket | None = None
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    stats: dict = field(default_factory=lambda: {
        "requests": 0, "rate_limited": 0, "peak_in_flight": 0, "lowest_allowed": None,
        "waited": 0, "wait_seconds": 0.0,
    })

    def __post_init__(self):
        self.allowed = float(self.limits.max_concurrent)
        if self.limits.requests_per_minute:
            self.requests = TokenBucket(self.limits.requests_per_minute)
        if self.limits.input_tokens_per_minute:
            self.tokens = TokenBucket(self.limits.input_tokens_per_minute)


class RateLimiter:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.states: dict[str, ProviderState] = {}

    def state(self, provider: str) -> ProviderState:
        if provider not in self.states:
            self.states[provider] = ProviderState(self.settings.limits(provider))
        return self.states[provider]

    @asynccontextmanager
    async def slot(self, provider: str, request: dict):
        state = self.state(provider)
        estimate = estimate_input_tokens(request) if state.tokens else 0
        started = time.monotonic()
        async with state.condition:
            while True:
                now = time.monotonic()
                waits = [state.paused_until - now]
                if state.in_flight >= max(1, int(state.allowed)):
                    waits.append(float("inf"))  # woken when a request finishes
                if state.requests:
                    waits.append(state.requests.wait_time(1))
                if state.tokens:
                    waits.append(state.tokens.wait_time(estimate))
                wait = max(waits)
                if wait <= 0:
                    break
                try:
                    await asyncio.wait_for(
                        state.condition.wait(), None if wait == float("inf") else wait)
                except asyncio.TimeoutError:
                    pass
            if state.requests:
                state.requests.take(1)
            if state.tokens:
                state.tokens.take(estimate)
            state.in_flight += 1
            state.stats["requests"] += 1
            state.stats["peak_in_flight"] = max(state.stats["peak_in_flight"], state.in_flight)
            waited = time.monotonic() - started
            if waited > 0.001:
                state.stats["waited"] += 1
                state.stats["wait_seconds"] += waited
        try:
            yield
        finally:
            async with state.condition:
                state.in_flight -= 1
                state.condition.notify(1)

    async def success(self, provider: str) -> None:
        state = self.state(provider)
        async with state.condition:
            before = int(state.allowed)
            state.allowed = round(min(float(state.limits.max_concurrent), state.allowed + INCREASE), 6)
            if int(state.allowed) > before:
                state.condition.notify(1)

    async def rate_limited(self, provider: str, retry_after: float | None) -> None:
        state = self.state(provider)
        async with state.condition:
            now = time.monotonic()
            state.stats["rate_limited"] += 1
            if now - state.last_decrease >= max(COOLDOWN_SECONDS, retry_after or 0):
                state.allowed = max(1.0, state.allowed * DECREASE)
                state.last_decrease = now
                lowest = state.stats["lowest_allowed"]
                state.stats["lowest_allowed"] = int(state.allowed) if lowest is None else min(lowest, int(state.allowed))
            if retry_after:
                state.paused_until = max(state.paused_until, now + retry_after)

    def snapshot(self) -> dict:
        snapshot = {}
        for name, state in self.states.items():
            stats = dict(state.stats)
            total_wait = stats.pop("wait_seconds")
            stats["mean_wait_seconds"] = round(total_wait / stats["waited"], 3) if stats["waited"] else 0.0
            stats.update(allowed_in_flight=int(state.allowed), in_flight=state.in_flight)
            snapshot[name] = stats
        return snapshot
