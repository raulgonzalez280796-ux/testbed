"""Scale test (not part of the unit suite):

    .venv/bin/python -m tests.scale_test <new empty folder> run
    .venv/bin/python -m tests.scale_test <same folder> resume <batch_dir>

1000 trials at once with the fake provider. Each trial: 2 agents x 5 turns,
each turn a tool call then text (20 model calls), 300 ms per call, 2% random
429s. Prints wall time, peak memory and status counts.
"""

import asyncio
import json
import os
import resource
import sys
import time
from pathlib import Path

import yaml

from testbed.batch import create_batch
from testbed.executors.local import run_batch_dir
from testbed.tools.library import starter_code

from . import fakes


def scenario(home: Path) -> Path:
    for kind in ("scenarios", "tools", "checks"):
        (home / kind).mkdir(parents=True, exist_ok=True)
    data = {
        "name": "scale",
        "files": [{"name": "notes.txt", "content": "start"}],
        "agents": [
            {"id": agent_id, "provider": "fake", "model": "scale", "turns": 5,
             "task_prompt": "Work.", "continuation_prompt": "Continue.",
             "tools": ["read_notes"]}
            for agent_id in ("A", "B")
        ],
    }
    (home / "tools" / "read_notes.py").write_text(starter_code("read", "read_notes", "notes.txt"))
    path = home / "scenarios" / "scale.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    (home / "settings.yaml").write_text("rate_limits:\n  fake:\n    max_concurrent: 1000\n")
    return path


def setup_fake():
    fakes.install()
    fakes.FakeAdapter.latency = 0.3
    fakes.FakeAdapter.rate_limit_probability = float(os.environ.get("SCALE_429", "0.02"))
    fakes.SCRIPTS["scale"] = [
        step for _ in range(5)
        for step in ({"tool": "read_notes"}, "Noted.")
    ]


def peak_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


async def main():
    home = Path(sys.argv[1])
    setup_fake()
    if sys.argv[2] == "run":
        batch_dir = create_batch(home, scenario(home), trials=1000, parallel=1000, seed=1)
        print(f"batch {batch_dir}", flush=True)
    else:
        batch_dir = Path(sys.argv[3])
    started = time.monotonic()
    summary = await run_batch_dir(batch_dir, home, keys={})
    print(json.dumps({
        "seconds": round(time.monotonic() - started, 1), "peak_rss_mb": round(peak_mb()),
        "counts": summary["counts"], "running_peak": summary["running_peak"],
        "rate_limits": summary["rate_limits"],
    }, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
