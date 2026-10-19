import json
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from testbed.logger import read_events

from . import fakes

REPO = Path(__file__).resolve().parents[1]


def agent(agent_id, model=None, **fields):
    data = {
        "id": agent_id, "provider": "fake", "model": model or f"script-{agent_id}",
        "task_prompt": f"Task for {agent_id}.", "continuation_prompt": "Continue.",
        "turns": 2, "tools": [],
    }
    data.update(fields)
    return data


class HomeTestCase(unittest.IsolatedAsyncioTestCase):
    """A temporary project folder with the repo's example tools and checks."""

    def setUp(self):
        fakes.install()
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        for kind in ("scenarios", "tools", "checks"):
            (self.home / kind).mkdir()
        for path in (REPO / "tools").glob("*.py"):
            shutil.copy(path, self.home / "tools")
        for path in (REPO / "checks").glob("*.py"):
            shutil.copy(path, self.home / "checks")

    def tearDown(self):
        self.tmp.cleanup()

    def scenario(self, name="test", **fields) -> Path:
        data = {"name": name, "agents": [agent("A")], **fields}
        path = self.home / "scenarios" / f"{name}.yaml"
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return path

    def write_code(self, kind: str, name: str, source: str) -> Path:
        path = self.home / kind / f"{name}.py"
        path.write_text(source, encoding="utf-8")
        return path

    async def run_scenario(self, path: Path, trials=1, parallel=1, **options):
        from testbed.batch import create_batch
        from testbed.executors.local import run_batch_dir

        batch_dir = create_batch(self.home, path, trials=trials, parallel=parallel, seed=options.pop("seed", 7))
        summary = await run_batch_dir(batch_dir, self.home, keys=options.pop("keys", {}), **options)
        return batch_dir, summary

    @staticmethod
    def result(batch_dir: Path, index=1) -> dict:
        return json.loads((batch_dir / f"trial_{index:04d}" / "result.json").read_text())

    @staticmethod
    def events(batch_dir: Path, index=1) -> list[dict]:
        return read_events(batch_dir / f"trial_{index:04d}")

    @staticmethod
    def of(events, event_type):
        return [e["data"] for e in events if e["event_type"] == event_type]
