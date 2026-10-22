"""The trial script: a trial's events as readable steps, in order (testbed/script.py)."""

import json
from datetime import datetime, timedelta, timezone

from testbed.cli import main
from testbed.logger import read_events
from testbed.script import (
    GROUPS, activity, build_script, clock, filter_steps, script_html, script_markdown, trial_script,
)

from . import fakes
from .helpers import HomeTestCase, agent

FILES = [{"name": "log.txt"}]  # no output.txt, so read_output fails


class ScriptTests(HomeTestCase):
    async def trial(self, **fields):
        path = self.scenario(**fields)
        batch_dir, _ = await self.run_scenario(path)
        trial_dir = batch_dir / "trial_0001"
        manifest = json.loads((trial_dir / "manifest.json").read_text())
        return trial_dir, read_events(trial_dir), manifest

    async def environment_trial(self):
        fakes.SCRIPTS["script-A"] = [
            {"text": "Posting.", "tools": [{"tool": "write_log", "args": {"text": "help"}},
                                           {"tool": "read_output"}]},
            "Done.",
        ]
        fakes.SCRIPTS["script-B"] = [{"tool": "read_log"}, "I read it."]
        return await self.trial(files=FILES, agents=[
            agent("A", turns=1, tools=["write_log", "read_output"], system_prompt="Be brief."),
            agent("B", turns=1, tools=["read_log"]),
        ])

    async def test_steps_follow_the_log_and_pair_tools_with_results(self):
        trial_dir, events, manifest = await self.environment_trial()
        steps = build_script(events, manifest)
        self.assertEqual([s.seq for s in steps], sorted(s.seq for s in steps))
        self.assertEqual([(s.kind, s.agent) for s in steps], [
            ("start", None), ("round", None),
            ("input", "A"), ("reply", "A"), ("tool", "A"), ("tool", "A"), ("reply", "A"), ("turn_end", "A"),
            ("input", "B"), ("reply", "B"), ("tool", "B"), ("reply", "B"), ("turn_end", "B"),
            ("end", None),
        ])
        first_input = steps[2]
        self.assertEqual([s.label for s in first_input.sections],
                         ["System prompt (sent with every call)", "Tools it can use", "Input"])
        self.assertEqual(first_input.sections[0].body, "Be brief.")
        self.assertIn("write_log: Append text followed by a newline to log.txt.", first_input.sections[1].body)
        write, read = steps[4], steps[5]
        self.assertEqual((write.title, write.status, write.meta.split(" · ")[:2]),
                         ('A called write_log(text="help")', "ok", ["log.txt", "append"]))
        self.assertEqual(write.sections[-1].body, "Text appended.")
        self.assertEqual((read.status, read.sections[-1].label), ("error", "Error (WorkspaceError)"))
        self.assertIn("No file named 'output.txt'", read.sections[-1].body)
        reply = steps[3]
        self.assertEqual((reply.title, reply.sections[0].body), ("A wrote", "Posting."))
        self.assertTrue(reply.meta.startswith("call 1 · 100 tokens"))
        self.assertEqual([event["event_type"] for event in reply.raw], ["model_call", "agent_reply"])
        self.assertEqual(steps[-1].meta.split(" · ")[:2], ["status: completed", "stop reason: round_limit"])

    async def test_conversation_messages_are_shown_apart_from_the_instruction(self):
        fakes.SCRIPTS["script-A"] = ["Hello B."]
        fakes.SCRIPTS["script-B"] = ["Hi A."]
        _, events, manifest = await self.trial(interaction="conversation", agents=[
            agent("A", turns=1), agent("B", turns=1, task_prompt="Answer A.")])
        b_input = next(s for s in build_script(events, manifest) if s.kind == "input" and s.agent == "B")
        sections = {s.label: s.body for s in b_input.sections}
        self.assertEqual(sections["Messages from the other agents"], "[A] (round 1): Hello B.")
        self.assertEqual(sections["Instruction"], "Answer A.")

    async def test_logs_from_before_the_instruction_field_show_the_whole_input(self):
        _, events, manifest = await self.environment_trial()
        for event in events:
            event["data"].pop("instruction", None)
        a_input = next(s for s in build_script(events, manifest) if s.kind == "input")
        self.assertEqual(a_input.sections[-1].label, "Input")
        self.assertEqual(a_input.sections[-1].body, "Task for A.")

    async def test_a_running_trial(self):
        _, events, manifest = await self.environment_trial()
        cut = next(i for i, e in enumerate(events) if e["event_type"] == "tool_started")
        running = events[:cut + 1]
        steps = build_script(running, manifest)
        self.assertEqual((steps[-1].kind, steps[-1].status), ("tool", "running"))
        now = datetime.fromisoformat(running[-1]["timestamp"]) + timedelta(seconds=4)
        self.assertEqual(activity(running, now), "A is running write_log (4 s)")
        waiting = events[:next(i for i, e in enumerate(events) if e["event_type"] == "turn_started") + 1]
        self.assertTrue(activity(waiting, datetime.now(timezone.utc)).startswith("A is waiting for the model"))
        self.assertEqual(activity([], datetime.now(timezone.utc)), "starting")

    async def test_filters_and_search(self):
        _, events, manifest = await self.environment_trial()
        steps = build_script(events, manifest)
        only_b = filter_steps(steps, agents=["B"])
        self.assertEqual({s.agent for s in only_b}, {None, "B"})  # rounds, start and end stay
        tools = filter_steps(steps, groups=["Tool calls"])
        self.assertEqual([s.tool for s in tools], ["write_log", "read_output", "read_log"])
        self.assertEqual([s.tool for s in filter_steps(tools, tools=["read_log"])], ["read_log"])
        found = filter_steps(steps, text="HELP")
        self.assertEqual([s.title for s in found], ['A called write_log(text="help")', "B called read_log()"])
        self.assertEqual(filter_steps(steps, rounds=(2, 3)), [s for s in steps if s.round is None])
        self.assertEqual(set(GROUPS), {"Inputs", "Replies", "Tool calls", "Notes and problems",
                                       "Rounds and turn ends"})

    async def test_downloads_keep_text_exact_and_safe(self):
        fakes.SCRIPTS["script-A"] = ["Here is code:\n```\nx = 1\n```\n<script>alert(1)</script>"]
        trial_dir, events, manifest = await self.trial(agents=[agent("A", turns=1)])
        steps = build_script(events, manifest)
        markdown = script_markdown(steps, {"title": "Trial 1"})
        self.assertIn("````\nHere is code:\n```\nx = 1\n```", markdown)  # a longer fence around ```
        page = script_html(steps, {"title": "Trial 1"})
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn("status: completed", trial_script(trial_dir))
        out = self.home / "trial.html"
        self.assertEqual(main(["script", str(trial_dir), "--format", "html", "--out", str(out)]), 0)
        self.assertTrue(out.read_text().startswith("<!doctype html>"))
        self.assertEqual(main(["script", str(self.home)]), 2)

    def test_clock(self):
        self.assertEqual(clock(None), "")
        self.assertEqual(clock(4.25), "+00:04.2")
        self.assertEqual(clock(125), "+02:05.0")
        self.assertEqual(clock(3725), "+1:02:05")
