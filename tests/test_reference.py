"""The scenario reference and JSON Schema come from the code and stay in sync with it."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from testbed.cli import main
from testbed.reference import EXAMPLE, SECTIONS, render_reference
from testbed.scenario import Scenario

REFERENCE = Path(__file__).resolve().parents[1] / "docs" / "scenario-reference.md"


class ReferenceTests(unittest.TestCase):
    def test_every_field_is_described(self):
        for title, model, _ in SECTIONS:
            for name, field in model.model_fields.items():
                with self.subTest(section=title, field=name):
                    self.assertTrue((field.description or "").strip())

    def test_example_is_a_valid_scenario(self):
        Scenario.model_validate(yaml.safe_load(EXAMPLE))

    def test_committed_reference_matches_the_code(self):
        self.assertEqual(
            REFERENCE.read_text(encoding="utf-8"), render_reference(),
            "docs/scenario-reference.md is out of date: run "
            "`python -m testbed reference --out docs/scenario-reference.md`",
        )

    def test_schema_command(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "scenario.schema.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["schema", "--out", str(out)]), 0)
            schema = json.loads(out.read_text())
        self.assertEqual(set(schema["properties"]), set(Scenario.model_fields))
        self.assertEqual(schema["required"], ["name", "agents"])
        self.assertIn("AgentSpec", schema["$defs"])


if __name__ == "__main__":
    unittest.main()
