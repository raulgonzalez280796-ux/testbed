"""Python API, for scripts and notebooks (Jupyter, Colab).

    from testbed import run_batch
    summary = run_batch("scenarios/help_seeking.yaml", trials=30, parallel=10, yes=True)

Batches above 20 trials need `yes=True`. Without it, a 3-trial pilot runs if
there is no earlier data for the scenario, and ConfirmationRequired is raised
with the projected token use, so you can decide before spending more.
"""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

from .batch import create_batch
from .executors.local import run_batch_dir
from .pilot import PILOT_TRIALS, ConfirmationRequired, plan_for_path


def resolve_home(home: str | Path | None = None) -> Path:
    return Path(home or os.environ.get("TESTBED_HOME") or Path.cwd()).resolve()


def resolve_scenario(home: Path, scenario: str | Path) -> Path:
    path = Path(scenario)
    if not path.is_absolute() and not path.exists():
        path = home / path
    return path


async def arun_pilot(scenario: str | Path, *, home: str | Path | None = None) -> dict:
    home_dir = resolve_home(home)
    batch_dir = create_batch(
        home_dir, resolve_scenario(home_dir, scenario),
        trials=PILOT_TRIALS, parallel=PILOT_TRIALS, label="pilot",
    )
    return await run_batch_dir(batch_dir, home_dir)


async def arun_batch(
    scenario: str | Path, *, trials: int = 1, parallel: int = 1, seed: int | None = None,
    home: str | Path | None = None, label: str = "", yes: bool = False,
) -> dict:
    home_dir = resolve_home(home)
    path = resolve_scenario(home_dir, scenario)
    plan = plan_for_path(home_dir, path, trials)
    if plan["needs_confirmation"] and not yes:
        if plan["needs_pilot"]:
            await arun_pilot(path, home=home_dir)
            plan = plan_for_path(home_dir, path, trials)
        raise ConfirmationRequired(plan)
    batch_dir = create_batch(
        home_dir, path, trials=trials, parallel=parallel, seed=seed, label=label,
        projection=plan["projection"],
    )
    return await run_batch_dir(batch_dir, home_dir)


async def aresume_batch(batch_dir: str | Path, *, home: str | Path | None = None) -> dict:
    return await run_batch_dir(Path(batch_dir), resolve_home(home))


def _run(coro) -> dict:
    """asyncio.run, also from inside a running loop (Jupyter) via a helper thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict = {}

    def target():
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as error:  # re-raised in the caller's thread
            box["error"] = error

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


def run_batch(scenario: str | Path, **options) -> dict:
    return _run(arun_batch(scenario, **options))


def run_pilot(scenario: str | Path, **options) -> dict:
    return _run(arun_pilot(scenario, **options))


def resume_batch(batch_dir: str | Path, **options) -> dict:
    return _run(aresume_batch(batch_dir, **options))
