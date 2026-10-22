"""Command line: python -m testbed <command> ...

  run SCENARIO [--trials M] [--parallel N] [--seed S]   create a batch and run it
  resume BATCH_DIR                                      re-run trials that did not complete
  cancel BATCH_DIR                                      ask a running batch to stop
  status BATCH_DIR                                      print the batch summary
  recheck BATCH_DIR                                     re-run checks on saved trials (no API)
  validate SCENARIO                                     check the file and its tools/checks
  preview SCENARIO                                      what each agent will receive
  keys                                                  which provider keys are set
  init [FOLDER]                                         create a project folder with examples
  ui [--port P]                                         open the web UI (on this computer only)

The project folder (scenarios/, tools/, checks/, runs/, .env) is --home,
else $TESTBED_HOME, else the current folder.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import providers
from .api import resolve_home


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m testbed", description="llmArena testbed")
    parser.add_argument("--home", help="project folder (default: $TESTBED_HOME or current folder)")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="create a batch and run it")
    run.add_argument("scenario")
    run.add_argument("--trials", type=int, default=1, help="M: trials in total")
    run.add_argument("--parallel", type=int, default=1, help="N: trials at once")
    run.add_argument("--seed", type=int)
    run.add_argument("--label", default="")
    run.add_argument("--yes", action="store_true",
                     help="skip the confirmation for batches above 20 trials")
    for name in ("resume", "cancel", "status", "recheck"):
        sub = commands.add_parser(name)
        sub.add_argument("batch_dir")
    for name in ("validate", "preview"):
        sub = commands.add_parser(name)
        sub.add_argument("scenario")
    commands.add_parser("keys")
    init = commands.add_parser("init", help="create a project folder with the example templates")
    init.add_argument("folder", nargs="?", default=".")
    ui = commands.add_parser("ui", help="open the web UI")
    ui.add_argument("--port", type=int, default=8501)
    ui.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    home = resolve_home(args.home)

    try:
        return COMMANDS[args.command](args, home)
    except KeyboardInterrupt:
        print("\nStopped. Completed trials are kept; run `resume` to finish the batch.")
        return 130


def cmd_run(args, home: Path) -> int:
    from .api import arun_pilot, resolve_scenario
    from .batch import create_batch
    from .executors.local import MissingKeys
    from .pilot import describe, plan_for_path

    scenario_path = resolve_scenario(home, args.scenario)
    try:
        plan = plan_for_path(home, scenario_path, args.trials)
        if plan["needs_confirmation"] and not args.yes:
            if plan["needs_pilot"]:
                print("No earlier trials of this scenario: running a 3-trial pilot to project tokens.")
                try:
                    asyncio.run(arun_pilot(scenario_path, home=home))
                except MissingKeys as error:
                    print(f"{error}. Add them in the UI's Settings page or in {home / '.env'}.",
                          file=sys.stderr)
                    return 2
                plan = plan_for_path(home, scenario_path, args.trials)
            print(describe(plan))
            if not sys.stdin.isatty():
                print("Re-run with --yes to start the batch.", file=sys.stderr)
                return 2
            if input("Start the batch? [y/N] ").strip().lower() not in ("y", "yes"):
                print("Not started.")
                return 1
        batch_dir = create_batch(
            home, scenario_path, trials=args.trials, parallel=args.parallel,
            seed=args.seed, label=args.label, projection=plan["projection"],
        )
    except Exception as error:
        print(f"Cannot start: {error}", file=sys.stderr)
        return 2
    print(f"Batch: {batch_dir}")
    return _run(batch_dir, home)


def _run(batch_dir: Path, home: Path) -> int:
    from .executors.local import MissingKeys, run_batch_dir

    try:
        summary = asyncio.run(run_batch_dir(batch_dir, home))
    except MissingKeys as error:
        print(f"{error}. Add them in the UI's Settings page or in {home / '.env'}.", file=sys.stderr)
        return 2
    counts = ", ".join(f"{k}: {v}" for k, v in sorted(summary["counts"].items()))
    print(f"Finished ({summary['status']}) in {summary['seconds']} s — {counts}; "
          f"{summary['tokens_total']:,} tokens")
    if summary.get("message"):
        print(summary["message"])
    print(f"Summary: {batch_dir / 'summary.csv'}")
    return 0 if summary["status"] == "finished" else 1


def cmd_resume(args, home: Path) -> int:
    return _run(Path(args.batch_dir), home)


def cmd_cancel(args, home: Path) -> int:
    from .batch import request_cancel

    request_cancel(Path(args.batch_dir))
    print("Cancel requested.")
    return 0


def cmd_status(args, home: Path) -> int:
    from .batch import read_summary

    summary = read_summary(Path(args.batch_dir))
    if summary is None:
        print("No summary yet.")
        return 1
    summary = {key: value for key, value in summary.items() if key != "trials"}
    print(json.dumps(summary, indent=2))
    return 0


def cmd_recheck(args, home: Path) -> int:
    from .batch import Summary, load_batch
    from .keys import load_keys
    from .trial import BatchContext, recheck_trial

    batch_dir = Path(args.batch_dir)
    spec, scenario = load_batch(batch_dir)
    batch = BatchContext(batch_dir, spec["batch_id"], scenario, spec["seed"], load_keys(home))
    summary = Summary(batch_dir, spec, scenario)
    for index in summary.rows:
        trial_dir = batch.trial_dir(index)
        if (trial_dir / "result.json").exists():
            recheck_trial(batch, trial_dir)
            summary.refresh(index)
    summary.status = (json.loads((batch_dir / "summary.json").read_text()) or {}).get("status", "finished") \
        if (batch_dir / "summary.json").exists() else "finished"
    summary.save()
    print(f"Re-checked. Summary: {batch_dir / 'summary.csv'}")
    return 0


def cmd_validate(args, home: Path) -> int:
    from .batch import validate_code
    from .keys import load_keys, missing_keys
    from .scenario import load_scenario

    try:
        scenario = load_scenario(args.scenario)
        unknown = [name for name in scenario.providers if not providers.is_known(name)]
        if unknown:
            raise ValueError(f"unknown providers: {unknown} (known: {providers.provider_names()})")
        validate_code(home, scenario)
    except Exception as error:
        print(f"Invalid: {error}", file=sys.stderr)
        return 2
    missing = missing_keys(scenario.providers, load_keys(home))
    print(f"OK: {scenario.name} ({len(scenario.agents)} agents, {scenario.interaction} mode)")
    if missing:
        print(f"Note: running it needs {', '.join(missing)}.")
    return 0


def cmd_preview(args, home: Path) -> int:
    from .preview import preview, preview_text
    from .scenario import load_scenario

    print(preview_text(preview(home, load_scenario(args.scenario))))
    return 0


def cmd_keys(args, home: Path) -> int:
    from .keys import key_source, load_keys, masked

    keys = load_keys(home)
    for name in providers.provider_names():
        info = providers.provider_info(name)
        state = f"set ({masked(keys[name])}, from {key_source(home, name)})" if name in keys else "not set"
        print(f"{info.label:<14} {info.env_key:<20} {state}")
    return 0


def cmd_init(args, home: Path) -> int:
    from .project import init_project

    folder = Path(args.folder).resolve()
    created = init_project(folder)
    print(f"Project folder: {folder}")
    for name in created:
        print(f"  added {name}")
    print("Next: add an API key (python -m testbed ui, Settings), or copy .env.example to .env.")
    return 0


def cmd_ui(args, home: Path) -> int:
    import os
    import subprocess

    from .project import ensure_project

    ensure_project(home)
    app = Path(__file__).with_name("ui") / "app.py"
    env = {**os.environ, "TESTBED_HOME": str(home)}
    command = [
        sys.executable, "-m", "streamlit", "run", str(app),
        "--server.address", "127.0.0.1",  # this computer only: the UI has no login
        "--server.port", str(args.port),
        "--browser.gatherUsageStats", "false",
        "--client.toolbarMode", "minimal",
        "--server.headless", "true" if args.no_browser else "false",
    ]
    print(f"Testbed UI for {home} at http://localhost:{args.port}")
    return subprocess.call(command, env=env)


COMMANDS = {
    "run": cmd_run, "resume": cmd_resume, "cancel": cmd_cancel, "status": cmd_status,
    "recheck": cmd_recheck, "validate": cmd_validate, "preview": cmd_preview, "keys": cmd_keys,
    "init": cmd_init, "ui": cmd_ui,
}
