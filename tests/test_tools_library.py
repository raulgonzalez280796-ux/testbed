"""The tools library: one tool per file, @tool(file=, op=), the code check, notes, starters."""

import json
import random
import tempfile
import unittest
from pathlib import Path

import yaml

from testbed.batch import create_batch, load_batch
from testbed.executors.local import run_batch_dir
from testbed.project import TEMPLATES, init_project
from testbed.scenario import draw_variables, trial_seed
from testbed.tools.check_code import tool_name_in
from testbed.tools.library import (
    STARTERS, LibraryError, check_name, delete_tool, duplicate_tool, load_notes, merge_notes,
    set_note, starter_code, used_by,
)
from testbed.tools.loader import CodeError, has_errors, inspect_file, resolve_agent_tools, resolve_tool

from . import fakes
from .helpers import HomeTestCase, agent

GOOD = """from testbed import ToolContext, tool


@tool(file="notes.txt", op="append")
def add_note(ctx: ToolContext, text: str) -> str:
    \"\"\"Add a note.\"\"\"
    ctx.files.append("notes.txt", text)
    return "ok"
"""


def check(source: str, stem: str) -> dict:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"{stem}.py"
        path.write_text(source, encoding="utf-8")
        return inspect_file(path, kind="tools")


def found(report: dict) -> list[tuple[str, int | None, str]]:
    return [(item["level"], item["line"], item["message"]) for item in report["problems"]]


class CodeCheckTests(unittest.TestCase):
    def test_a_good_tool_has_no_problems(self):
        report = check(GOOD, "add_note")
        self.assertEqual(report["problems"], [])
        self.assertEqual([(t["ref"], t["file"], t["op"]) for t in report["tools"]], [("add_note", "notes.txt", "append")])

    def test_syntax_error_with_its_line(self):
        report = check("from testbed import tool\n\ndef broken(:\n    pass\n", "broken")
        self.assertFalse(report["ok"])
        self.assertEqual(found(report)[0][:2], ("error", 3))
        self.assertIn("SyntaxError", found(report)[0][2])

    def test_pyflakes_warnings(self):
        source = GOOD.replace("from testbed import", "import os\nfrom testbed import").replace(
            'return "ok"', "return missing_name")
        problems = found(check(source, "add_note"))
        self.assertIn(("warning", 1, "'os' imported but unused"), problems)
        self.assertTrue(any(level == "warning" and line == 9 and "undefined name 'missing_name'" in message
                            for level, line, message in problems))
        self.assertFalse(has_errors(check(source, "add_note")))

    def test_one_tool_per_file(self):
        source = GOOD + "\n\n@tool\ndef other(ctx: ToolContext) -> str:\n    \"\"\"Other.\"\"\"\n    return ''\n"
        level, line, message = found(check(source, "add_note"))[0]
        self.assertEqual((level, line), ("error", 12))
        self.assertIn("2 @tool functions (add_note, other)", message)

    def test_name_must_match_the_file(self):
        level, line, message = found(check(GOOD, "notes"))[0]
        self.assertEqual((level, line), ("error", 5))
        self.assertIn("called add_note but the file is notes.py", message)

    def test_missing_hints_docstring_and_return_type(self):
        source = "from testbed import ToolContext, tool\n\n\n@tool\ndef loose(ctx: ToolContext, text):\n    return text\n"
        problems = found(check(source, "loose"))
        self.assertEqual([(level, line) for level, line, _ in problems],
                         [("error", 4), ("error", 5), ("warning", 5), ("warning", 5)])
        self.assertIn("Importing the file failed", problems[0][2])
        self.assertIn("The argument text needs a type hint", problems[1][2])
        self.assertIn("No docstring", problems[2][2])
        self.assertIn("No return type", problems[3][2])

    def test_bad_file_and_op(self):
        source = GOOD.replace('file="notes.txt", op="append"', 'file="../x", op="delete"')
        messages = [message for level, _, message in found(check(source, "add_note")) if level == "error"]
        self.assertTrue(any("file= must be a plain file name" in m for m in messages))
        self.assertTrue(any("op= must be one of" in m for m in messages))

    def test_an_import_error_has_its_line(self):
        report = check("from testbed import tool\n\nraise RuntimeError('nope')\n", "boom")
        self.assertFalse(report["ok"])
        self.assertIn(("error", 3, "Importing the file failed: RuntimeError: nope"), found(report))

    def test_every_predefined_tool_is_clean(self):
        for path in sorted((TEMPLATES / "tools").glob("*.py")):
            with self.subTest(tool=path.stem):
                report = inspect_file(path, kind="tools")
                self.assertEqual(report["problems"], [])
                self.assertEqual([t["ref"] for t in report["tools"]], [path.stem])

    def test_tool_name_in(self):
        self.assertEqual(tool_name_in(GOOD), "add_note")
        self.assertIsNone(tool_name_in("def x(:"))
        self.assertIsNone(tool_name_in("x = 1\n"))


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.tools = self.home / "tools"
        (self.home / "scenarios").mkdir()
        self.tools.mkdir()
        (self.tools / "add_note.py").write_text(GOOD)

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_starter_passes_the_check(self):
        for kind in STARTERS:
            with self.subTest(starter=kind):
                report = check(starter_code(kind, "my_tool", "notes.txt"), "my_tool")
                self.assertEqual(report["problems"], [])
                self.assertEqual([t["name"] for t in report["tools"]], ["my_tool"])
        with self.assertRaises(LibraryError):
            starter_code("read", "my_tool", "")

    def test_names(self):
        self.assertEqual(check_name(" read_log ", self.tools), "read_log")
        for bad in ("read log", "1tool", "_hidden", "class", "add_note"):
            with self.subTest(name=bad), self.assertRaises(LibraryError):
                check_name(bad, self.tools)

    def test_notes_are_kept_apart_from_code(self):
        set_note(self.tools, "add_note", "For the diary scenario.")
        merge_notes(self.tools, {"add_note": "replaced?", "other": "Other tool."})
        self.assertEqual(load_notes(self.tools), {"add_note": "For the diary scenario.", "other": "Other tool."})
        set_note(self.tools, "other", "")
        self.assertEqual(load_notes(self.tools), {"add_note": "For the diary scenario."})
        self.assertTrue((self.tools / "notes.yaml").read_text().startswith("# Your notes"))

    def test_duplicate_renames_the_function_and_copies_the_note(self):
        set_note(self.tools, "add_note", "Diary.")
        path = duplicate_tool(self.tools, "add_note", "add_entry")
        self.assertEqual(tool_name_in(path.read_text()), "add_entry")
        self.assertEqual(check(path.read_text(), "add_entry")["problems"], [])
        self.assertEqual(load_notes(self.tools)["add_entry"], "Diary.")
        with self.assertRaises(LibraryError):
            duplicate_tool(self.tools, "add_note", "add_entry")

    def test_delete_removes_the_file_and_its_note(self):
        set_note(self.tools, "add_note", "Diary.")
        delete_tool(self.tools, "add_note")
        self.assertFalse((self.tools / "add_note.py").exists())
        self.assertEqual(load_notes(self.tools), {})

    def test_used_by(self):
        (self.home / "scenarios" / "a.yaml").write_text(yaml.safe_dump({"agents": [
            {"id": "A", "tools": ["add_note", "read_log"]}, {"id": "B", "tools": ["read_log"]}]}))
        (self.home / "scenarios" / "b.yaml").write_text(yaml.safe_dump({"agents": [
            {"id": "C", "tools": ["add_note:add_note"]}]}))
        (self.home / "scenarios" / "broken.yaml").write_text(": not yaml : [")
        self.assertEqual(used_by(self.home / "scenarios", "add_note"), {"a.yaml": ["A"], "b.yaml": ["C"]})
        self.assertEqual(used_by(self.home / "scenarios", "read_log"), {"a.yaml": ["A", "B"]})

    def test_init_adds_predefined_tools_and_their_notes_without_touching_yours(self):
        set_note(self.tools, "read_log", "My own words.")
        init_project(self.home)
        notes = load_notes(self.tools)
        self.assertEqual(notes["read_log"], "My own words.")
        self.assertIn("write_counter", notes)
        self.assertTrue((self.tools / "write_counter.py").exists())
        self.assertEqual((self.tools / "add_note.py").read_text(), GOOD)


class ResolveTests(HomeTestCase):
    def test_a_tool_is_named_by_its_file(self):
        self.assertEqual(resolve_tool("read_log", self.home / "tools").ref, "read_log")
        with self.assertRaisesRegex(CodeError, "no tool called 'read_nothing': there is no file tools/read_nothing.py"):
            resolve_tool("read_nothing", self.home / "tools")

    def test_a_file_with_several_tools_still_works(self):
        self.write_code("tools", "pair", (
            "from testbed import tool\n@tool\ndef ping() -> str:\n    'Ping.'\n    return 'pong'\n"
            "@tool\ndef pong() -> str:\n    'Pong.'\n    return 'ping'\n"))
        tools = resolve_agent_tools(["pair:ping", "pair:pong", "read_log"], self.home / "tools", "A")
        self.assertEqual([t.ref for t in tools.values()], ["pair:ping", "pair:pong", "read_log"])

    def test_unknown_agent_tool_is_reported(self):
        path = self.scenario(files=[{"name": "log.txt"}], agents=[agent("A", tools=["read_log", "read_nothing"])])
        with self.assertRaisesRegex(CodeError, "no tool called 'read_nothing'"):
            create_batch(self.home, path, trials=1, parallel=1)

    def test_a_batch_freezes_only_the_tools_it_uses(self):
        path = self.scenario(files=[{"name": "log.txt"}], agents=[agent("A", tools=["read_log", "write_log"])])
        batch_dir = create_batch(self.home, path, trials=1, parallel=1)
        self.assertEqual(sorted(p.name for p in (batch_dir / "code" / "tools").iterdir()),
                         ["read_log.py", "write_log.py"])


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
