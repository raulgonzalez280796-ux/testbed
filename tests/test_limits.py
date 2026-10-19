"""Retries, rate limiting and the token bucket (offline)."""

import asyncio
import time
import unittest

from testbed.executors.ratelimit import RateLimiter, TokenBucket
from testbed.settings import ProviderLimits, Settings

from . import fakes
from .helpers import HomeTestCase, agent


class RetryTests(HomeTestCase):
    async def test_rate_limit_is_retried_and_logged(self):
        fakes.SCRIPTS["script-A"] = [{"error": "rate_limit", "status": 429, "retry_after": 0.01,
                                       "times": 2, "then": "Recovered."}]
        path = self.scenario(agents=[agent("A", turns=1)])
        batch_dir, summary = await self.run_scenario(path)
        self.assertEqual(summary["counts"], {"completed": 1})
        events = self.events(batch_dir)
        self.assertEqual(len(self.of(events, "rate_limited")), 2)
        self.assertEqual(self.of(events, "model_call")[0]["attempts"], 3)
        self.assertEqual(summary["rate_limits"]["fake"]["rate_limited"], 2)

    async def test_retries_run_out_into_an_infra_error(self):
        fakes.SCRIPTS["script-A"] = [{"error": "server", "status": 503, "retry_after": 0.01}]
        path = self.scenario(limits={"retry_attempts": 2}, agents=[agent("A", turns=1)])
        batch_dir, summary = await self.run_scenario(path)
        self.assertEqual(summary["counts"], {"infra_error": 1})
        failed = self.of(self.events(batch_dir), "model_call_failed")[0]
        self.assertEqual(failed["attempts"], 3)

    async def test_auth_errors_are_not_retried(self):
        fakes.SCRIPTS["script-A"] = [{"error": "auth", "status": 401}]
        path = self.scenario(agents=[agent("A", turns=1)])
        batch_dir, _ = await self.run_scenario(path)
        self.assertEqual(self.of(self.events(batch_dir), "model_call_failed")[0]["attempts"], 1)

    async def test_in_flight_cap_holds_across_parallel_trials(self):
        fakes.FakeAdapter.latency = 0.03
        (self.home / "settings.yaml").write_text("rate_limits:\n  fake:\n    max_concurrent: 3\n")
        path = self.scenario(agents=[agent("A", turns=2)])
        _, summary = await self.run_scenario(path, trials=20, parallel=20)
        self.assertEqual(summary["counts"], {"completed": 20})
        self.assertEqual(summary["rate_limits"]["fake"]["peak_in_flight"], 3)
        self.assertEqual(summary["rate_limits"]["fake"]["requests"], 40)


class LimiterUnitTests(unittest.IsolatedAsyncioTestCase):
    def test_token_bucket(self):
        bucket = TokenBucket(60)  # one per second, burst of 60
        self.assertEqual(bucket.wait_time(60), 0.0)
        bucket.take(60)
        self.assertAlmostEqual(bucket.wait_time(1), 1.0, delta=0.05)

    async def test_429_halves_the_in_flight_cap_and_pauses(self):
        limiter = RateLimiter(Settings(rate_limits={"p": ProviderLimits(max_concurrent=8)}))
        await limiter.rate_limited("p", retry_after=0.2)
        self.assertEqual(limiter.snapshot()["p"]["allowed_in_flight"], 5)  # 8 * 0.7
        await limiter.rate_limited("p", retry_after=0.2)  # same burst: no second cut
        self.assertEqual(limiter.snapshot()["p"]["allowed_in_flight"], 5)
        started = time.monotonic()
        async with limiter.slot("p", {}):
            pass
        self.assertGreaterEqual(time.monotonic() - started, 0.15)
        for _ in range(40):
            await limiter.success("p")
        self.assertEqual(limiter.snapshot()["p"]["allowed_in_flight"], 7)  # 5.6 + 40 * 0.05

    async def test_requests_per_minute_paces_calls(self):
        limiter = RateLimiter(Settings(rate_limits={"p": ProviderLimits(requests_per_minute=600)}))
        limiter.state("p").requests.level = 0  # empty bucket: 10 per second
        started = time.monotonic()
        for _ in range(3):
            async with limiter.slot("p", {}):
                pass
        self.assertGreaterEqual(time.monotonic() - started, 0.25)


if __name__ == "__main__":
    unittest.main()


class PilotTests(HomeTestCase):
    async def test_big_batch_runs_a_pilot_then_asks(self):
        from testbed.api import arun_batch
        from testbed.pilot import ConfirmationRequired

        path = self.scenario(agents=[agent("A", turns=2)])
        with self.assertRaises(ConfirmationRequired) as caught:
            await arun_batch(path, trials=100, parallel=10, home=self.home)
        plan = caught.exception.plan
        self.assertEqual(plan["projection"]["pilot_trials"], 3)
        self.assertEqual(plan["projection"]["projected_median"], 100 * 200)
        self.assertIn("no batch token cap", " ".join(plan["warnings"]))
        # The pilot is reused: asking again runs no new trials.
        calls = len(fakes.REQUESTS)
        with self.assertRaises(ConfirmationRequired):
            await arun_batch(path, trials=100, parallel=10, home=self.home)
        self.assertEqual(len(fakes.REQUESTS), calls)
        summary = await arun_batch(path, trials=25, parallel=25, home=self.home, yes=True)
        self.assertEqual(summary["counts"], {"completed": 25})

    async def test_small_batches_need_no_confirmation(self):
        from testbed.api import arun_batch

        path = self.scenario()
        summary = await arun_batch(path, trials=20, parallel=5, home=self.home)
        self.assertEqual(summary["counts"], {"completed": 20})

    def test_cli_refuses_without_yes_when_not_interactive(self):
        from testbed.cli import main

        path = self.scenario()
        import io, contextlib, sys
        stdin = sys.stdin
        sys.stdin = io.StringIO("")  # not a terminal
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = main(["--home", str(self.home), "run", str(path), "--trials", "50"])
        finally:
            sys.stdin = stdin
        self.assertEqual(code, 2)
