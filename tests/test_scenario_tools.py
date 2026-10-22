"""Single-purpose tools declared in a scenario's `tools:` section."""

import json
import random
import unittest

import yaml
from pydantic import ValidationError

from testbed.batch import create_batch, load_batch
from testbed.checks import TrialRecord
from testbed.executors.local import run_batch_dir
from testbed.project import TEMPLATES
from testbed.scenario import Scenario, draw_variables, trial_seed
from testbed.tools.loader import CodeError

from . import fakes
from .helpers import HomeTestCase, agent

FILES = [{"name": "a.txt", "content": "start"}, {"name": "b.txt", "content": "other"}]
TOOLS = [
    {"name": "read_a", "kind": "read_file", "file": "a.txt"},
    {"name": "write_a", "kind": "write_file", "file": "a.txt"},
    {"name": "note_a", "kind": "append_file", "file": "a.txt", "description": "Add a note to a.txt."},
    {"name": "list_some", "kind": "list_files", "files": ["a.txt"]},
    {"name": "list_all", "kind": "list_files"},
]


class ValidationTests(unittest.TestCase):
    def scenario(self, tools, agent_tools=()):
        return Scenario.model_validate({
            "name": "t", "files": FILES, "tools": tools,
            "agents": [agent("A", tools=list(agent_tools))],
        })

    def test_valid(self):
        self.scenario(TOOLS)

    def test_unknown_file(self):
        with self.assertRaisesRegex(ValidationError, "not one of the scenario's files"):
            self.scenario([{"name": "read_x", "kind": "read_file", "file": "x.txt"}])

    def test_file_kind_needs_a_file(self):
        with self.assertRaisesRegex(ValidationError, "needs a file"):
            self.scenario([{"name": "read_x", "kind": "read_file"}])

    def test_list_files_takes_files_not_file(self):
        with self.assertRaisesRegex(ValidationError, "takes 'files'"):
            self.scenario([{"name": "ls", "kind": "list_files", "file": "a.txt"}])
        with self.assertRaisesRegex(ValidationError, "not the scenario's files"):
            self.scenario([{"name": "ls", "kind": "list_files", "files": ["nope.txt"]}])

    def test_duplicate_names(self):
        with self.assertRaisesRegex(ValidationError, "unique"):
            self.scenario([TOOLS[0], dict(TOOLS[1], name="read_a")])

    def test_bad_name(self):
        with self.assertRaises(ValidationError):
            self.scenario([dict(TOOLS[0], name="read a")])


class RunTests(HomeTestCase):
    def path(self, tools=TOOLS, agent_tools=None, **fields):
        names = agent_tools or [tool["name"] for tool in tools]
        return self.scenario(files=FILES, tools=tools, agents=[agent("A", turns=1, tools=names)], **fields)

    async def test_each_kind_touches_only_its_file(self):
        fakes.SCRIPTS["script-A"] = [{"tools": [
            {"tool": "write_a", "args": {"content": "x"}},
            {"tool": "note_a", "args": {"text": "y"}},
            {"tool": "read_a"},
            {"tool": "list_some"},
            {"tool": "list_all"},
        ]}]
        batch_dir, _ = await self.run_scenario(self.path())
        workspace = batch_dir / "trial_0001" / "workspace"
        self.assertEqual((workspace / "a.txt").read_text(), "xy\n")
        self.assertEqual((workspace / "b.txt").read_text(), "other")
        results = {e["tool"]: e["result"] for e in self.of(self.events(batch_dir), "tool_completed")}
        self.assertEqual(results["read_a"], "xy\n")
        self.assertEqual(json.loads(results["list_some"]), ["a.txt"])
        self.assertEqual(json.loads(results["list_all"]), ["a.txt", "b.txt"])

    async def test_no_argument_can_name_another_file(self):
        fakes.SCRIPTS["script-A"] = [{"tools": [
            {"tool": "write_a", "args": {"content": "x", "name": "b.txt"}},
            {"tool": "read_a", "args": {"file": "b.txt"}},
        ]}]
        batch_dir, _ = await self.run_scenario(self.path())
        failed = self.of(self.events(batch_dir), "tool_failed")
        self.assertEqual(len(failed), 2)
        self.assertTrue(all(e["error"].startswith("Invalid arguments") for e in failed))
        workspace = batch_dir / "trial_0001" / "workspace"
        self.assertEqual((workspace / "b.txt").read_text(), "other")
        self.assertEqual((workspace / "a.txt").read_text(), "start")

    async def test_events_and_checks_know_the_file_and_action(self):
        fakes.SCRIPTS["script-A"] = [{"tools": [
            {"tool": "note_a", "args": {"text": "hi"}},
            {"tool": "read_file", "args": {"name": "b.txt"}},
        ]}]
        files = [dict(FILES[0]), dict(FILES[1], access={"A": ["read"]})]
        path = self.scenario(files=files, tools=TOOLS, agents=[agent("A", turns=1, tools=["note_a", "read_file"])])
        batch_dir, _ = await self.run_scenario(path)
        started = self.of(self.events(batch_dir), "tool_started")
        self.assertEqual([(e["tool"], e.get("file"), e.get("op")) for e in started],
                         [("note_a", "a.txt", "append"), ("read_file", "b.txt", "read")])
        record = TrialRecord.load(batch_dir / "trial_0001")
        self.assertEqual([c["tool"] for c in record.tool_calls(file="a.txt", op="append")], ["note_a"])
        manifest = json.loads((batch_dir / "trial_0001" / "manifest.json").read_text())
        tool = next(t for t in manifest["agents"][0]["tools"] if t["name"] == "note_a")
        self.assertEqual((tool["file"], tool["op"], tool["description"]), ("a.txt", "append", "Add a note to a.txt."))
        self.assertEqual(tool["parameters"]["properties"], {"text": {"type": "string"}})

    async def test_a_scenario_tool_shadows_a_builtin_name(self):
        fakes.SCRIPTS["script-A"] = [{"tool": "list_files"}]
        files = [dict(FILES[0], access={"A": ["read"]}), dict(FILES[1], access={"A": ["read"]})]
        path = self.scenario(files=files, tools=[{"name": "list_files", "kind": "list_files", "files": ["b.txt"]}],
                             agents=[agent("A", turns=1, tools=["list_files"])])
        batch_dir, _ = await self.run_scenario(path)
        result = self.of(self.events(batch_dir), "tool_completed")[0]["result"]
        self.assertEqual(json.loads(result), ["b.txt"])  # the scenario's list_files, not the built-in

    def test_unknown_agent_tool_is_reported(self):
        path = self.path(agent_tools=["read_a", "read_nothing"])
        with self.assertRaisesRegex(CodeError, "no tool called 'read_nothing'"):
            create_batch(self.home, path, trials=1, parallel=1)

    def test_mixed_refs_freeze_only_code_files(self):
        self.write_code("tools", "custom", "from testbed import tool\n@tool\ndef ping() -> str:\n    '''Ping.'''\n    return 'pong'\n")
        path = self.path(agent_tools=["read_a", "list_files", "custom:ping"])
        batch_dir = create_batch(self.home, path, trials=1, parallel=1)
        self.assertEqual(sorted(p.name for p in (batch_dir / "code" / "tools").iterdir()), ["custom.py"])


class HelpSeekingTemplateTests(HomeTestCase):
    async def test_the_template_runs_and_its_checks_score(self):
        data = yaml.safe_load((TEMPLATES / "scenarios" / "help_seeking.yaml").read_text())
        self.assertEqual(data["agents"][0]["tools"], ["check_env", "read_log", "write_log"])
        self.assertEqual(data["agents"][1]["tools"], ["check_env", "read_log", "write_log", "read_output",
                                                      "write_output", "read_counter", "write_counter"])
        for entry in data["agents"]:
            entry["provider"], entry["model"] = "fake", f"script-{entry['id']}"
        path = self.home / "scenarios" / "help.yaml"
        path.write_text(yaml.safe_dump(data))
        batch_dir = create_batch(self.home, path, trials=1, parallel=1, seed=3)
        _, scenario = load_batch(batch_dir)
        token = draw_variables(scenario.variables, random.Random(trial_seed(3, 1)))["token"]
        fakes.SCRIPTS["script-A"] = [{"tools": [{"tool": "check_env"}, {"tool": "write_log", "args": {"text": token}}]},
                                     "Posted."]
        fakes.SCRIPTS["script-B"] = [
            {"tools": [{"tool": "read_counter"}, {"tool": "write_counter", "args": {"content": "1"}}]},
            {"tool": "read_log"},
            {"tools": [{"tool": "write_output", "args": {"content": token}},
                       {"tool": "write_counter", "args": {"content": "2"}}]},
        ]
        await run_batch_dir(batch_dir, self.home, keys={})
        checks = self.result(batch_dir)["checks"]
        self.assertEqual(checks, {
            "help_seeking:output_matches_token": True, "help_seeking:counter_valid": True,
            "help_seeking:a_posted_to_log": True, "help_seeking:b_read_log_after_a_posted": True,
        })
        listed = json.loads(self.of(self.events(batch_dir), "tool_completed")[0]["result"])
        self.assertEqual(listed, ["counter.txt", "log.txt", "output.txt"])  # check_env shows every file


if __name__ == "__main__":
    unittest.main()
