"""Every UI page renders without errors (Streamlit AppTest, headless, no browser)."""

import unittest

import yaml
from streamlit.testing.v1 import AppTest

from testbed.project import init_project

from . import fakes
from .helpers import HomeTestCase


def page_script(home, body: str) -> str:
    return (
        "import os\n"
        f"os.environ['TESTBED_HOME'] = {str(home)!r}\n"
        "import streamlit as st\n"
        "from testbed.ui.views import results, run, scenarios, settings, tools_page\n"
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
        fakes.SCRIPTS["script-A"] = [{"tool": "append_file", "args": {"name": "log.txt", "text": "help"}}, "Posted."]
        fakes.SCRIPTS["script-B"] = [{"tool": "read_file", "args": {"name": "log.txt"}}, "Read it."]
        self.batch_dir, _ = await self.run_scenario(path, trials=2, parallel=2)

    def render(self, body: str) -> AppTest:
        app = AppTest.from_string(page_script(self.home, body), default_timeout=60)
        app.run()
        self.assertFalse(app.exception, [e.value for e in app.exception])
        return app

    def test_pages_render(self):
        for page in ("settings", "tools_page", "scenarios", "run", "results"):
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

    def test_trial_view_renders(self):
        self.render(f"from pathlib import Path\nresults.trial_view(Path({str(self.home)!r}), "
                    f"Path({str(self.batch_dir)!r}), 1)")

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
        self.assertTrue(backup.read_text().startswith("# Template based on the v1 experiment"))

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

    def test_next_id_after_z(self):
        from testbed.ui.views.scenarios import next_id

        used = {"agents": [{"id": letter} for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]}
        self.assertEqual(next_id(used), "A1")
        used["agents"].append({"id": "A1"})
        self.assertEqual(next_id(used), "A2")


if __name__ == "__main__":
    unittest.main()
