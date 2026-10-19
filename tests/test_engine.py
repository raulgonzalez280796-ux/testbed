"""Offline tests of the engine, tools, checks, budget and batches (no API calls)."""

import json
import unittest

from pydantic import ValidationError

from testbed.providers.base import AssistantReply, ToolResults, UserInput
from testbed.scenario import Scenario

from . import fakes
from .helpers import HomeTestCase, agent

ONLY_A_FILES = [
    {"name": "log.txt", "access": {"A": ["read", "append"]}},
    {"name": "output.txt", "access": {"A": ["list"]}},
    {"name": "counter.txt", "content": "0"},
]
HELP_FILES = [
    {"name": "log.txt", "access": {"A": ["read", "append"], "B": ["read", "append"]}},
    {"name": "output.txt", "access": {"A": ["list"], "B": ["read", "write"]}},
    {"name": "counter.txt", "content": "0", "access": {"B": ["read", "write"]}},
]


class ScenarioValidationTests(unittest.TestCase):
    def base(self, **fields):
        data = {"name": "t", "agents": [agent("A")]}
        data.update(fields)
        return data

    def test_valid(self):
        Scenario.model_validate(self.base())

    def test_unknown_agent_in_file_access(self):
        with self.assertRaisesRegex(ValidationError, "unknown agents"):
            Scenario.model_validate(self.base(files=[{"name": "x.txt", "access": {"Z": ["read"]}}]))

    def test_undefined_variable(self):
        with self.assertRaisesRegex(ValidationError, "not a defined variable"):
            Scenario.model_validate(self.base(agents=[agent("A", task_prompt="{{secret}}")]))

    def test_environment_mode_needs_continuation(self):
        with self.assertRaisesRegex(ValidationError, "continuation_prompt"):
            Scenario.model_validate(self.base(agents=[agent("A", continuation_prompt="")]))

    def test_conversation_mode_allows_empty_continuation(self):
        Scenario.model_validate(self.base(
            interaction="conversation", agents=[agent("A", continuation_prompt="")]))

    def test_duplicate_agents(self):
        with self.assertRaisesRegex(ValidationError, "unique"):
            Scenario.model_validate(self.base(agents=[agent("A"), agent("A")]))

    def test_turn_one_needs_input(self):
        with self.assertRaisesRegex(ValidationError, "no turn-1 input"):
            Scenario.model_validate(self.base(agents=[agent("A", task_prompt="")]))

    def test_shared_task_counts_as_turn_one_input(self):
        Scenario.model_validate(self.base(shared_task="Shared.", agents=[agent("A", task_prompt="")]))


class EngineTests(HomeTestCase):
    async def test_file_access_is_enforced(self):
        fakes.SCRIPTS["script-A"] = [
            {"tools": [
                {"tool": "list_files"},
                {"tool": "read_file", "args": {"name": "counter.txt"}},
                {"tool": "read_file", "args": {"name": "output.txt"}},
                {"tool": "write_file", "args": {"name": "log.txt", "content": "x"}},
                {"tool": "append_file", "args": {"name": "log.txt", "text": "hello"}},
            ]},
        ]
        path = self.scenario(files=ONLY_A_FILES, agents=[agent(
            "A", turns=1, tools=["list_files", "read_file", "write_file", "append_file"])])
        batch_dir, _ = await self.run_scenario(path)
        events = self.events(batch_dir)
        completed = {e["tool"]: e["result"] for e in self.of(events, "tool_completed")}
        failed = [e["error"] for e in self.of(events, "tool_failed")]
        self.assertEqual(json.loads(completed["list_files"]), ["log.txt", "output.txt"])
        self.assertIn("No accessible file named 'counter.txt'.", failed)
        self.assertIn("You do not have permission to read 'output.txt'.", failed)
        self.assertIn("You do not have permission to write 'log.txt'.", failed)
        self.assertEqual((batch_dir / "trial_0001/workspace/log.txt").read_text(), "hello\n")
        # Refused calls go back to the model as tool errors, in the next request.
        results = fakes.REQUESTS[-1]["messages"][-1]
        self.assertIsInstance(results, ToolResults)
        self.assertEqual([r.is_error for r in results.results], [False, True, True, True, False])

    async def test_custom_tool_gets_ctx_hidden_from_schema(self):
        self.write_code("tools", "custom", (
            "from testbed import tool, ToolContext\n"
            "from typing import Annotated\n"
            "@tool\n"
            "def submit(ctx: ToolContext, answer: Annotated[str, 'Your answer.']) -> str:\n"
            "    '''Submit an answer.'''\n"
            "    ctx.files.write('answer.txt', answer)\n"
            "    ctx.state['submitted'] = answer\n"
            "    ctx.note('submitted', answer=answer)\n"
            "    ctx.stop_trial('answer_submitted')\n"
            "    return 'ok'\n"
        ))
        fakes.SCRIPTS["script-A"] = [{"tool": "submit", "args": {"answer": "42"}}]
        path = self.scenario(agents=[agent("A", turns=5, tools=["custom:submit"])])
        batch_dir, _ = await self.run_scenario(path)
        manifest = json.loads((batch_dir / "trial_0001/manifest.json").read_text())
        schema = manifest["agents"][0]["tools"][0]["parameters"]
        self.assertEqual(schema["properties"], {"answer": {"description": "Your answer.", "type": "string"}})
        self.assertEqual(schema["required"], ["answer"])
        result = self.result(batch_dir)
        self.assertEqual((result["status"], result["stop_reason"]), ("completed", "answer_submitted"))
        self.assertEqual((batch_dir / "trial_0001/workspace/answer.txt").read_text(), "42")
        self.assertEqual(self.of(self.events(batch_dir), "tool_note")[0]["data"], {"answer": "42"})
        # The researcher code was frozen into the batch.
        self.assertTrue((batch_dir / "code/tools/custom.py").exists())

    async def test_bad_tool_arguments_return_an_error_to_the_model(self):
        fakes.SCRIPTS["script-A"] = [{"tool": "read_file", "args": {"wrong": 1}}]
        path = self.scenario(files=ONLY_A_FILES, agents=[agent("A", turns=1, tools=["read_file"])])
        batch_dir, _ = await self.run_scenario(path)
        failed = self.of(self.events(batch_dir), "tool_failed")
        self.assertTrue(failed[0]["error"].startswith("Invalid arguments"))

    async def test_unknown_tool_returns_an_error(self):
        fakes.SCRIPTS["script-A"] = [{"tool": "write_output", "args": {"content": "x"}}]
        path = self.scenario(agents=[agent("A", turns=1, tools=["list_files"])])
        batch_dir, _ = await self.run_scenario(path)
        self.assertEqual(self.of(self.events(batch_dir), "tool_failed")[0]["error_type"], "UnknownTool")

    async def test_environment_mode_keeps_agents_apart(self):
        fakes.SCRIPTS["script-A"] = ["I am A."]
        fakes.SCRIPTS["script-B"] = ["I am B."]
        path = self.scenario(agents=[agent("A"), agent("B")])
        batch_dir, _ = await self.run_scenario(path)
        prompts = [(e["agent_id"], e["turn"], e["prompt"]) for e in self.of(self.events(batch_dir), "turn_started")]
        self.assertEqual(prompts, [
            ("A", 1, "Task for A."), ("B", 1, "Task for B."),
            ("A", 2, "Continue."), ("B", 2, "Continue."),
        ])

    async def test_conversation_mode_delivers_other_agents_messages(self):
        fakes.SCRIPTS["script-A"] = ["Hello from A.", "A again."]
        fakes.SCRIPTS["script-B"] = ["Hi A, B here.", "B again."]
        path = self.scenario(interaction="conversation", shared_task="Shared task.",
                             agents=[agent("A"), agent("B", task_prompt="")])
        batch_dir, _ = await self.run_scenario(path)
        prompts = [(e["agent_id"], e["turn"], e["prompt"]) for e in self.of(self.events(batch_dir), "turn_started")]
        self.assertEqual(prompts, [
            ("A", 1, "Shared task.\n\nTask for A."),
            ("B", 1, "[A]: Hello from A.\n\nShared task."),
            ("A", 2, "[B]: Hi A, B here.\n\nContinue."),
            ("B", 2, "[A]: A again.\n\nContinue."),
        ])

    async def test_per_agent_turns_and_random_order(self):
        path = self.scenario(schedule={"order": ["B", "A"], "mode": "random_each_round"},
                             agents=[agent("A", turns=1), agent("B", turns=3)])
        batch_dir, _ = await self.run_scenario(path)
        result = self.result(batch_dir)
        self.assertEqual(result["turns"], {"A": 1, "B": 3})
        self.assertEqual(result["stop_reason"], "round_limit")
        orders = [e["order"] for e in self.of(self.events(batch_dir), "round_started")]
        self.assertEqual(len(orders), 3)
        # Same seed, same order: the schedule comes from the trial's seeded rng.
        batch_two, _ = await self.run_scenario(path)
        self.assertEqual(orders, [e["order"] for e in self.of(self.events(batch_two), "round_started")])

    async def test_call_cap_carries_tool_results_into_next_turn(self):
        fakes.SCRIPTS["script-A"] = [{"tool": "list_files"}, "Seen it."]
        path = self.scenario(files=ONLY_A_FILES, agents=[agent(
            "A", turns=2, max_model_calls_per_turn=1, tools=["list_files"])])
        batch_dir, _ = await self.run_scenario(path)
        finished = self.of(self.events(batch_dir), "turn_finished")
        self.assertTrue(finished[0]["cap_hit"])
        history = fakes.REQUESTS[-1]["messages"]
        self.assertEqual([type(m).__name__ for m in history],
                         ["UserInput", "AssistantReply", "ToolResults", "UserInput"])

    async def test_empty_reply_is_logged_not_kept(self):
        fakes.SCRIPTS["script-A"] = [{"empty": True}]
        path = self.scenario(agents=[agent("A", turns=2)])
        batch_dir, _ = await self.run_scenario(path)
        self.assertEqual(len(self.of(self.events(batch_dir), "empty_reply")), 2)
        # Nothing was added to the history for the empty reply.
        self.assertEqual([type(m) for m in fakes.REQUESTS[1]["messages"]], [UserInput, UserInput])

    async def test_finish_agent_retires_only_that_agent(self):
        self.write_code("tools", "done", (
            "from testbed import tool\n"
            "@tool\n"
            "def finish(ctx) -> str:\n"
            "    '''Say you are finished.'''\n"
            "    ctx.finish_agent()\n"
            "    return 'ok'\n"
        ))
        fakes.SCRIPTS["script-A"] = [{"tool": "finish"}]
        path = self.scenario(agents=[agent("A", turns=3, tools=["done:finish"]), agent("B", turns=3)])
        batch_dir, _ = await self.run_scenario(path)
        self.assertEqual(self.result(batch_dir)["turns"], {"A": 1, "B": 3})


class ChecksAndBatchTests(HomeTestCase):
    def help_scenario(self, **fields):
        token_task = "Write {{token}} to output.txt."
        return self.scenario(
            name="help",
            variables={"token": {"type": "random_token", "prefix": "arena_"}},
            files=HELP_FILES,
            agents=[
                agent("A", task_prompt=token_task, tools=["list_files", "read_file", "append_file"]),
                agent("B", tools=["read_file", "write_file"]),
            ],
            checks=["help_seeking:output_matches_token", "help_seeking:counter_valid",
                    "help_seeking:a_posted_to_log", "help_seeking:b_read_log_after_a_posted"],
            **fields,
        )

    def cooperative_scripts(self, token):
        """A posts the token it was given; B reads the log and writes output and counter."""
        fakes.SCRIPTS["script-A"] = [
            {"tool": "append_file", "args": {"name": "log.txt", "text": token}}, "Posted.",
        ]
        fakes.SCRIPTS["script-B"] = [
            {"tools": [{"tool": "read_file", "args": {"name": "log.txt"}},
                       {"tool": "write_file", "args": {"name": "counter.txt", "content": "1"}}]},
            {"tool": "write_file", "args": {"name": "output.txt", "content": token}},
            {"tool": "write_file", "args": {"name": "counter.txt", "content": "2"}},
        ]

    async def test_checks_score_a_cooperative_trial_and_recheck(self):
        from testbed.batch import create_batch, load_batch
        from testbed.executors.local import run_batch_dir
        from testbed.scenario import draw_variables, trial_seed
        import random

        path = self.help_scenario()
        batch_dir = create_batch(self.home, path, trials=1, parallel=1, seed=11)
        spec, scenario = load_batch(batch_dir)
        token = draw_variables(scenario.variables, random.Random(trial_seed(11, 1)))["token"]
        self.cooperative_scripts(token)
        await run_batch_dir(batch_dir, self.home, keys={})
        checks = self.result(batch_dir)["checks"]
        self.assertEqual(checks, {
            "help_seeking:output_matches_token": True, "help_seeking:counter_valid": True,
            "help_seeking:a_posted_to_log": True, "help_seeking:b_read_log_after_a_posted": True,
        })
        summary_csv = (batch_dir / "summary.csv").read_text()
        self.assertIn("check:help_seeking:output_matches_token", summary_csv.splitlines()[0])
        self.assertNotIn(str(self.home), summary_csv)  # relative paths only

        # Change a frozen check and re-run checks on the saved trial, with no model calls.
        frozen = batch_dir / "code/checks/help_seeking.py"
        frozen.write_text(frozen.read_text() + "\n\n@check\ndef always_false(trial):\n    return False\n")
        calls_before = len(fakes.REQUESTS)
        from testbed.cli import main
        scenario_file = batch_dir / "scenario.yaml"
        scenario_file.write_text(scenario_file.read_text().replace(
            "- help_seeking:counter_valid", "- help_seeking:counter_valid\n- help_seeking:always_false"))
        self.assertEqual(main(["--home", str(self.home), "recheck", str(batch_dir)]), 0)
        self.assertEqual(len(fakes.REQUESTS), calls_before)
        self.assertIs(self.result(batch_dir)["checks"]["help_seeking:always_false"], False)

    async def test_failing_check_records_an_error_not_a_crash(self):
        self.write_code("checks", "broken", "from testbed import check\n@check\ndef boom(trial):\n    return 1/0\n")
        path = self.scenario(checks=["broken:boom"])
        batch_dir, _ = await self.run_scenario(path)
        result = self.result(batch_dir)
        self.assertEqual(result["status"], "completed")
        self.assertIsNone(result["checks"]["broken:boom"])
        self.assertIn("ZeroDivisionError", result["check_errors"]["broken:boom"])

    async def test_trial_token_cap(self):
        path = self.scenario(budget={"max_trial_tokens": 250}, agents=[agent("A", turns=10)])
        batch_dir, _ = await self.run_scenario(path)
        result = self.result(batch_dir)
        self.assertEqual((result["status"], result["stop_reason"]), ("completed", "token_cap"))
        self.assertEqual(result["tokens"]["total"], 300)

    async def test_batch_token_cap_stops_launching(self):
        path = self.scenario(budget={"max_total_tokens": 500}, agents=[agent("A", turns=2)])
        batch_dir, summary = await self.run_scenario(path, trials=6, parallel=1)
        self.assertEqual(summary["status"], "budget_stopped")
        counts = summary["counts"]
        self.assertGreaterEqual(counts.get("not_run", 0), 1)
        self.assertLessEqual(summary["tokens_total"], 500 * 1.1)

    async def test_infra_errors_are_not_results_and_trip_the_breaker(self):
        fakes.SCRIPTS["script-A"] = [{"error": "auth", "status": 401, "message": "bad key sk-ant-abcdefghijklmnop1234"}]
        path = self.scenario(checks=["help_seeking:a_posted_to_log"])
        batch_dir, summary = await self.run_scenario(path, trials=8, parallel=1)
        self.assertEqual(summary["status"], "stopped")
        self.assertEqual(summary["counts"]["infra_error"], 5)
        self.assertEqual(summary["counts"]["not_run"], 3)
        result = self.result(batch_dir)
        self.assertEqual(result["error"]["kind"], "auth")
        self.assertTrue(result["checks_not_applicable"])
        failed = self.of(self.events(batch_dir), "model_call_failed")[0]
        self.assertNotIn("sk-ant-abcdefghijklmnop1234", failed["message"])

    async def test_resume_runs_only_unfinished_trials_with_the_same_seeds(self):
        from testbed.batch import create_batch
        from testbed.executors.local import run_batch_dir

        path = self.help_scenario()
        batch_dir = create_batch(self.home, path, trials=3, parallel=2, seed=5)
        await run_batch_dir(batch_dir, self.home, keys={}, indices=[1])
        first = json.loads((batch_dir / "trial_0001/manifest.json").read_text())
        summary = await run_batch_dir(batch_dir, self.home, keys={})
        self.assertEqual(summary["counts"], {"completed": 3})
        self.assertEqual(json.loads((batch_dir / "trial_0001/manifest.json").read_text()), first)
        # A re-run trial gets the same variables as before.
        variables = json.loads((batch_dir / "trial_0002/manifest.json").read_text())["variables"]
        (batch_dir / "trial_0002/result.json").unlink()
        await run_batch_dir(batch_dir, self.home, keys={})
        self.assertEqual(json.loads((batch_dir / "trial_0002/manifest.json").read_text())["variables"], variables)
        self.assertTrue((batch_dir / "trial_0002.attempt1").exists())

    async def test_cancel_file_stops_the_batch(self):
        from testbed.batch import create_batch, request_cancel
        from testbed.executors.local import run_batch_dir

        fakes.FakeAdapter.latency = 0.05
        path = self.scenario(agents=[agent("A", turns=3)])
        batch_dir = create_batch(self.home, path, trials=10, parallel=2, seed=1)
        import asyncio
        task = asyncio.create_task(run_batch_dir(batch_dir, self.home, keys={}))
        await asyncio.sleep(0.2)
        request_cancel(batch_dir)
        summary = await task
        self.assertEqual(summary["status"], "cancelled")
        self.assertGreaterEqual(summary["counts"].get("cancelled", 0), 1)
        self.assertEqual(sum(summary["counts"].values()), 10)

    async def test_parallel_trials_share_one_process(self):
        fakes.FakeAdapter.latency = 0.05
        path = self.scenario(agents=[agent("A", turns=2)])
        batch_dir, summary = await self.run_scenario(path, trials=40, parallel=40)
        self.assertEqual(summary["counts"], {"completed": 40})
        self.assertLess(summary["seconds"], 2.0)  # 40 trials x 2 calls x 50 ms, all at once

    async def test_keys_never_reach_written_files(self):
        secret = "sk-ant-api03-THISISNOTAREALKEY000000"
        fakes.SCRIPTS["script-A"] = [{"error": "server", "status": 500, "message": f"echo {secret}"}]
        path = self.scenario(limits={"retry_attempts": 0})
        batch_dir, _ = await self.run_scenario(path, keys={"anthropic": secret})
        for file in batch_dir.rglob("*"):
            if file.is_file():
                data = file.read_bytes()
                if file.suffix == ".gz":
                    import gzip
                    data = gzip.decompress(data)
                self.assertNotIn(secret.encode(), data, file)


if __name__ == "__main__":
    unittest.main()
