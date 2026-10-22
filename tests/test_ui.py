"""Every UI page renders without errors (Streamlit AppTest, headless, no browser)."""

import json
import unittest

import yaml
from streamlit.testing.v1 import AppTest

from testbed.project import init_project
from testbed.tools.library import load_notes, starter_code

from . import fakes
from .helpers import HomeTestCase


def page_script(home, body: str) -> str:
    return (
        "import os\n"
        f"os.environ['TESTBED_HOME'] = {str(home)!r}\n"
        "import streamlit as st\n"
        "from testbed.ui.views import checks_page, results, run, scenarios, settings, tools_page, trial_page\n"
        "st.session_state['pages'] = {}\n"
        f"{body}\n"
    )


class UiTests(HomeTestCase):
    async def asyncSetUp(self):
        init_project(self.home)
        data = yaml.safe_load((self.home / "scenarios" / "help_seeking.yaml").read_text())
        for agent in data["agents"]:
            agent["provider"], agent["model"] = "fake", f"script-{agent['id']}"
        path = self.home / "scenarios" / "fake_help.yaml"
        path.write_text(yaml.safe_dump(data))
        fakes.SCRIPTS["script-A"] = [{"tool": "write_log", "args": {"text": "help"}}, "Posted."]
        fakes.SCRIPTS["script-B"] = [{"tool": "read_log"}, "Read it."]
        self.batch_dir, _ = await self.run_scenario(path, trials=2, parallel=2)

    def render(self, body: str) -> AppTest:
        app = AppTest.from_string(page_script(self.home, body), default_timeout=60)
        app.run()
        self.assertFalse(app.exception, [e.value for e in app.exception])
        return app

    def test_pages_render(self):
        for page in ("settings", "tools_page", "checks_page", "scenarios", "run", "results", "trial_page"):
            with self.subTest(page=page):
                self.render(f"{page}.render()")

    def test_scenario_editor_opens_a_template_and_validates(self):
        app = self.render("scenarios.render()")
        app.run()  # the first run opens the first scenario file and reruns
        self.assertFalse(app.exception)
        texts = " ".join(element.value for element in app.success)
        self.assertIn("Valid scenario", texts)
        names = [box.value for box in app.text_input]
        self.assertIn("help-seeking", names)  # fake_help.yaml (first file) is open

    def trial_page(self, batch: str | None = None, trial: int | None = None) -> AppTest:
        app = AppTest.from_string(page_script(self.home, "trial_page.render()"), default_timeout=60)
        if batch is not None:
            app.query_params["batch"] = batch
            app.query_params["trial"] = str(trial)
        app.run()
        self.assertFalse(app.exception, [e.value for e in app.exception])
        return app

    def test_trial_page_shows_the_script_of_a_finished_trial(self):
        app = self.trial_page(self.batch_dir.name, 1)
        self.assertEqual(app.title[0].value, "Trial 1 of 2")
        lines = " ".join(m.value for m in app.markdown)
        self.assertIn("received", lines)
        self.assertIn('called write\\_log\\(text="help"\\)', lines)
        self.assertIn("Trial 2 →", [b.label for b in app.link_button])  # next; no previous on trial 1
        self.assertNotIn("← Trial 0", [b.label for b in app.link_button])
        self.assertEqual([d.label for d in app.get("download_button")][:2], ["Script .md", "Script .html"])
        app.text_input(key="script_search").set_value("help").run()
        self.assertTrue(any(" contain “help”" in c.value for c in app.caption))

    def test_trial_page_follows_a_running_trial(self):
        import gzip
        import shutil
        from datetime import datetime, timezone

        running = self.batch_dir.parent / "running_batch"
        shutil.copytree(self.batch_dir, running)
        trial_dir = running / "trial_0001"
        (trial_dir / "result.json").unlink()
        with gzip.open(trial_dir / "events.jsonl.gz", "rt") as stream:
            events = [json.loads(line) for line in stream if line.strip()]
        (trial_dir / "events.jsonl.gz").unlink()
        cut = next(i for i, e in enumerate(events) if e["event_type"] == "tool_started")
        now = datetime.now(timezone.utc).isoformat()
        with (trial_dir / "events.jsonl").open("w") as stream:
            for event in events[:cut]:
                stream.write(json.dumps({**event, "timestamp": now}) + "\n")
        app = self.trial_page("running_batch", 1)
        self.assertTrue(any("Running" in i.value and "A is about to run its tool calls" in i.value
                            for i in app.info), [i.value for i in app.info])
        self.assertIn("Script .md (so far)", [d.label for d in app.get("download_button")])

    def test_trial_page_without_a_trial_offers_a_picker(self):
        app = self.trial_page()
        self.assertEqual([s.label for s in app.selectbox], ["Batch", "Trial"])
        app = self.trial_page("../outside", 1)  # never a path
        self.assertEqual([s.label for s in app.selectbox], ["Batch", "Trial"])

    def test_results_links_every_trial_to_its_page(self):
        app = self.render(f"st.session_state['results_batch'] = {self.batch_dir.name!r}\nresults.render()")
        table = app.dataframe[-1].value
        self.assertEqual(list(table["open"]), [f"trial?batch={self.batch_dir.name}&trial={n}" for n in (1, 2)])

    def test_run_page_links_running_trials(self):
        import os

        summary_path = self.batch_dir / "summary.json"
        summary = json.loads(summary_path.read_text())
        summary["status"] = "running"
        summary["trials"][1]["status"] = "running"
        summary_path.write_text(json.dumps(summary))
        (self.batch_dir / "runner.pid").write_text(str(os.getpid()))  # a live process
        app = self.render("run.render()")
        links = {b.label: b.proto.url for b in app.link_button}
        self.assertEqual(links.get("Trial 2"), f"trial?batch={self.batch_dir.name}&trial=2")

    def test_interrupted_batch_is_offered_for_resume(self):
        import json
        summary_path = self.batch_dir / "summary.json"
        summary = json.loads(summary_path.read_text())
        summary["status"] = "running"  # as left behind when the runner process dies
        summary_path.write_text(json.dumps(summary))
        app = self.render("run.render()")
        self.assertIn("Resume", [button.label for button in app.button])
        self.assertTrue(any("not running" in w.value for w in app.warning))

    def test_run_page_with_a_selected_scenario(self):
        self.render("st.session_state['run_scenario'] = 'fake_help.yaml'\nrun.render()")


class EditorRegressionTests(HomeTestCase):
    """Bugs found while using the scenario editor."""

    def setUp(self):
        super().setUp()
        init_project(self.home)

    def editor(self, path=None) -> AppTest:
        opener = ""
        if path is not None:
            opener = ("from pathlib import Path\n"
                      "if 'opened' not in st.session_state:\n"
                      f"    scenarios.open_file(Path({str(path)!r}))\n"
                      "    st.session_state['opened'] = True\n")
        app = AppTest.from_string(page_script(self.home, opener + "scenarios.render()"), default_timeout=60)
        app.run()
        app.run()  # the first run may open a file and rerun
        self.assertFalse(app.exception, [e.value for e in app.exception])
        return app

    def problems(self, app: AppTest) -> list[str]:
        return [element.value for element in app.error]

    def saved(self, name: str) -> dict:
        return yaml.safe_load((self.home / "scenarios" / name).read_text())

    def test_duplicate_agent_then_save(self):
        app = self.editor(self.home / "scenarios" / "help_seeking.yaml")
        next(b for b in app.button if b.label == "Duplicate").click().run()
        self.assertFalse(app.exception, [e.value for e in app.exception])
        self.assertEqual(self.problems(app), [])
        next(b for b in app.button if b.label == "Save").click().run()
        self.assertFalse(app.exception)
        data = self.saved("help_seeking.yaml")
        self.assertEqual([a["id"] for a in data["agents"]], ["A", "C", "B"])
        self.assertEqual(data["schedule"]["order"], ["A", "B", "C"])
        # The template had comments, so the original was kept once as a .bak.
        backup = self.home / "scenarios" / "help_seeking.yaml.bak"
        self.assertTrue(backup.read_text().startswith("# The v1 experiment with predefined tools"))

    def test_save_as_copy_and_discard(self):
        app = self.editor(self.home / "scenarios" / "negotiation.yaml")
        next(b for b in app.button if b.label == "Duplicate").click().run()
        sidebar = " ".join(element.value for element in app.sidebar.markdown)
        self.assertIn("unsaved changes", sidebar)
        next(b for b in app.button if b.label == "Discard changes").click().run()
        self.assertEqual(len(app.session_state["editor"]["data"]["agents"]), 2)
        next(b for b in app.button if b.label == "Duplicate").click().run()
        next(box for box in app.text_input if box.label == "New file name").set_value("negotiation_three").run()
        next(b for b in app.button if b.label == "Save copy").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(self.saved("negotiation_three.yaml")["agents"]), 3)
        self.assertEqual(len(self.saved("negotiation.yaml")["agents"]), 2)  # original untouched

    def test_agent_missing_from_the_stored_order_is_added(self):
        # The state the owner hit: three agents, but C missing from schedule.order.
        data = self.saved("help_seeking.yaml")
        extra = dict(data["agents"][0], id="C")
        data["agents"].insert(1, extra)
        path = self.home / "scenarios" / "missing_order.yaml"
        path.write_text(yaml.safe_dump(data, sort_keys=False))
        app = self.editor(path)
        self.assertEqual(self.problems(app), [])
        self.assertEqual(app.session_state["editor"]["data"]["schedule"]["order"], ["A", "B", "C"])
        next(b for b in app.button if b.label == "Save").click().run()
        self.assertEqual(self.saved("missing_order.yaml")["schedule"]["order"], ["A", "B", "C"])

    def test_leaving_an_agent_out_of_the_order_puts_it_last(self):
        app = self.editor(self.home / "scenarios" / "help_seeking.yaml")
        order = next(m for m in app.multiselect if m.label.startswith("Turn order"))
        order.set_value(["B"]).run()
        self.assertEqual(self.problems(app), [])
        self.assertEqual(app.session_state["editor"]["data"]["schedule"]["order"], ["B", "A"])

    def test_renaming_an_agent_to_an_existing_id_is_refused(self):
        app = self.editor(self.home / "scenarios" / "help_seeking.yaml")
        ids = [box for box in app.text_input if box.label == "Id"]
        ids[1].set_value("A").run()
        self.assertFalse(app.exception, [e.value for e in app.exception])
        self.assertTrue(any("already called 'A'" in message for message in self.problems(app)))
        self.assertEqual([a["id"] for a in app.session_state["editor"]["data"]["agents"]], ["A", "B"])

    def test_untouched_file_has_no_unsaved_changes(self):
        app = self.editor(self.home / "scenarios" / "negotiation.yaml")
        sidebar = " ".join(element.value for element in app.sidebar.markdown)
        self.assertNotIn("unsaved changes", sidebar)

    def test_editor_has_no_tools_tab_or_access_grid(self):
        app = self.editor(self.home / "scenarios" / "help_seeking.yaml")
        self.assertEqual([tab.label for tab in app.tabs],
                         ["General", "Agents", "Files", "Variables", "Checks & budget", "Preview"])
        self.assertFalse([b for b in app.button if b.label.startswith("+ ")])
        self.assertFalse([t for t in app.toggle if "Access grid" in t.label])

    def test_agents_pick_from_every_tool_with_its_note(self):
        from testbed.tools.library import set_note

        set_note(self.home / "tools", "read_log", "My note about read_log.")
        app = self.editor(self.home / "scenarios" / "negotiation.yaml")
        picker = next(m for m in app.multiselect if m.label == "Tools")  # the Seller's
        self.assertIn("read_log — My note about read_log.", picker.options)
        self.assertIn("submit_secret — Secret keeper - checks the guess; a correct one ends the trial.",
                      picker.options)
        picker.set_value(list(picker.value) + ["read_log"]).run()
        next(b for b in app.button if b.label == "Save").click().run()
        self.assertIn("read_log", self.saved("negotiation.yaml")["agents"][0]["tools"])

    def test_an_old_scenario_is_updated_when_opened(self):
        data = self.saved("help_seeking.yaml")
        data["files"][0]["access"] = {"A": ["read", "append"]}
        data["tools"] = [{"name": "read_log", "kind": "read_file", "file": "log.txt"}]
        path = self.home / "scenarios" / "old.yaml"
        path.write_text(yaml.safe_dump(data, sort_keys=False))
        app = self.editor(path)
        warnings = " ".join(w.value for w in app.warning)
        self.assertIn("file access grid (on log.txt)", warnings)
        self.assertIn("defined its own tools (read_log)", warnings)
        self.assertIn("unsaved changes", " ".join(m.value for m in app.sidebar.markdown))
        self.assertEqual(self.problems(app), [])
        next(b for b in app.button if b.label == "Save").click().run()
        saved = self.saved("old.yaml")
        self.assertNotIn("tools", saved)
        self.assertNotIn("access", saved["files"][0])

    def test_help_seeking_shows_its_single_purpose_tools(self):
        app = self.editor(self.home / "scenarios" / "help_seeking.yaml")
        pickers = [m for m in app.multiselect if m.label == "Tools"]
        self.assertEqual([list(m.value) for m in pickers], [
            ["check_env", "read_log", "write_log"],
            ["check_env", "read_log", "write_log", "read_output", "write_output", "read_counter", "write_counter"],
        ])

    def test_next_id_after_z(self):
        from testbed.ui.views.scenarios import next_id

        used = {"agents": [{"id": letter} for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]}
        self.assertEqual(next_id(used), "A1")
        used["agents"].append({"id": "A1"})
        self.assertEqual(next_id(used), "A2")


class ToolsPageTests(HomeTestCase):
    """The Tools page: list, add, edit, check, note, duplicate, delete."""

    def setUp(self):
        super().setUp()
        init_project(self.home)
        self.tools = self.home / "tools"

    def page(self) -> AppTest:
        body = "st.session_state.setdefault('plain_editor', True)\ntools_page.render()"
        app = AppTest.from_string(page_script(self.home, body), default_timeout=60)
        app.run()
        self.assertFalse(app.exception, [e.value for e in app.exception])
        return app

    def open(self, app: AppTest, name: str) -> AppTest:
        next(s for s in app.selectbox if s.label == "Open a tool").set_value(name).run()
        self.assertFalse(app.exception, [e.value for e in app.exception])
        return app

    def code(self, app: AppTest):
        return next(box for box in app.text_area if box.label == "Code")

    def click(self, app: AppTest, label: str, key: str | None = None) -> AppTest:
        next(b for b in app.button if b.label == label and (key is None or b.key == key)).click().run()
        self.assertFalse(app.exception, [e.value for e in app.exception])
        return app

    def test_the_list_shows_every_tool_its_note_and_who_uses_it(self):
        table = self.page().dataframe[0].value
        rows = {row["Tool"]: row for row in table.to_dict("records")}
        self.assertEqual(set(rows), {path.stem for path in self.tools.glob("*.py")})
        self.assertEqual(rows["write_log"]["Check"], "✓ ok")
        self.assertEqual(rows["write_log"]["File"], "log.txt (append)")
        self.assertEqual(rows["write_log"]["The model is told"], "Append text followed by a newline to log.txt.")
        self.assertIn("help_seeking (A, B)", rows["write_log"]["Used by"])
        self.assertEqual(rows["submit_secret"]["Used by"], "secret_keeper (Requester)")
        self.assertTrue(rows["check_env"]["Your note"].startswith("Lists every file"))

    def test_add_a_tool_from_a_starter(self):
        app = self.click(self.page(), "Add tool")
        next(r for r in app.radio if r.label == "Starter").set_value("append").run()
        next(t for t in app.text_input if t.label == "Name").set_value("add_note").run()
        next(t for t in app.text_input if t.label == "The file it works on").set_value("diary.txt").run()
        next(t for t in app.text_input if t.label == "Your note (optional)").set_value("For the diary study.").run()
        self.click(app, "Create tool")
        source = (self.tools / "add_note.py").read_text()
        self.assertIn('@tool(file="diary.txt", op="append")\ndef add_note(', source)
        self.assertEqual(load_notes(self.tools)["add_note"], "For the diary study.")
        self.assertEqual(app.selectbox[0].value, "add_note")  # the new tool is open
        self.assertTrue(any("Created the tool add_note" in s.value for s in app.success))

    def test_paste_a_tool(self):
        app = self.click(self.page(), "Add tool")
        next(r for r in app.radio if r.label == "Start from").set_value("paste").run()
        pasted = starter_code("read", "read_diary", "diary.txt")
        self.code(app).set_value(pasted).run()
        self.assertTrue(any("tools/read_diary.py" in c.value for c in app.caption))
        self.click(app, "Save", key="tool_new_save")
        self.assertEqual((self.tools / "read_diary.py").read_text(), pasted)
        # The same name again is refused.
        app = self.click(self.page(), "Add tool")
        next(r for r in app.radio if r.label == "Start from").set_value("paste").run()
        self.code(app).set_value(pasted).run()
        self.click(app, "Save", key="tool_new_save")
        self.assertTrue(any("already exists" in e.value for e in app.error))

    def test_edit_check_and_save(self):
        app = self.open(self.page(), "read_log")
        original = (self.tools / "read_log.py").read_text()
        self.code(app).set_value(original.replace("def read_log(", "def read_logs(")).run()
        self.click(app, "Check", key="tool_check_read_log")
        self.assertTrue(any("called read_logs but the file is read_log.py" in e.value for e in app.error))
        self.click(app, "Save", key="tool_save_read_log")
        self.assertTrue(any("Not saved" in e.value for e in app.error))
        self.assertEqual((self.tools / "read_log.py").read_text(), original)
        edited = original.replace('"""Read the UTF-8 contents of log.txt."""', '"""Read the shared log."""')
        self.code(app).set_value(edited).run()
        self.click(app, "Save", key="tool_save_read_log")
        self.assertEqual((self.tools / "read_log.py").read_text(), edited)

    def test_note_duplicate_and_delete(self):
        app = self.open(self.page(), "write_output")
        next(t for t in app.text_input if t.label == "Your note").set_value("Only B has this.").run()
        self.assertEqual(load_notes(self.tools)["write_output"], "Only B has this.")
        next(t for t in app.text_input if t.label == "Name of the copy").set_value("write_answer").run()
        self.click(app, "Duplicate", key="tool_dup_write_output")
        copy = (self.tools / "write_answer.py").read_text()
        self.assertIn("def write_answer(ctx: ToolContext, content: str)", copy)
        self.assertEqual(load_notes(self.tools)["write_answer"], "Only B has this.")
        self.assertEqual(app.selectbox[0].value, "write_answer")
        # Deleting a tool that scenarios use says which ones.
        app = self.open(app, "write_output")
        self.assertTrue(any("help_seeking (B)" in w.value for w in app.warning))
        self.click(app, "Delete", key="tool_delete_write_output")
        self.assertFalse((self.tools / "write_output.py").exists())
        self.assertNotIn("write_output", load_notes(self.tools))

    def test_a_broken_file_is_listed_with_its_errors(self):
        (self.tools / "broken.py").write_text("from testbed import tool\n\n@tool\ndef broken(x):\n    return x\n")
        rows = {row["Tool"]: row for row in self.page().dataframe[0].value.to_dict("records")}
        self.assertTrue(rows["broken"]["Check"].startswith("✗"))
        app = self.open(self.page(), "broken")
        self.assertTrue(any("needs a type hint" in e.value for e in app.error))


if __name__ == "__main__":
    unittest.main()
