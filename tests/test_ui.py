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
        "from testbed.ui.pages import results, run, scenarios, settings, tools_page\n"
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

    def test_run_page_with_a_selected_scenario(self):
        self.render("st.session_state['run_scenario'] = 'fake_help.yaml'\nrun.render()")


if __name__ == "__main__":
    unittest.main()
