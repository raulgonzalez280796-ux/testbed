"""Command line: python -m testbed <command> ...

  run SCENARIO [--trials M] [--parallel N] [--seed S]   create a batch and run it
  resume BATCH_DIR                                      re-run trials that did not complete
  cancel BATCH_DIR                                      ask a running batch to stop
  status BATCH_DIR                                      print the batch summary
  recheck BATCH_DIR                                     re-run checks on saved trials (no API)
  validate SCENARIO                                     check the file and its tools/checks
  preview SCENARIO                                      what each agent will receive
  keys                                                  which provider keys are set

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
    for name in ("resume", "cancel", "status", "recheck"):
        sub = commands.add_parser(name)
        sub.add_argument("batch_dir")
    for name in ("validate", "preview"):
        sub = commands.add_parser(name)
        sub.add_argument("scenario")
    commands.add_parser("keys")
    args = parser.parse_args(argv)
    home = resolve_home(args.home)

    try:
        return COMMANDS[args.command](args, home)
    except KeyboardInterrupt:
        print("\nStopped. Completed trials are kept; run `resume` to finish the batch.")
        return 130


def cmd_run(args, home: Path) -> int:
    from .batch import create_batch
    from .executors.local import MissingKeys, run_batch_dir

    scenario_path = Path(args.scenario)
    if not scenario_path.is_absolute() and not scenario_path.exists():
        scenario_path = home / scenario_path
    try:
        batch_dir = create_batch(
            home, scenario_path, trials=args.trials, parallel=args.parallel,
            seed=args.seed, label=args.label,
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
        print(f"{info.label:<10} {info.env_key:<20} {state}")
    return 0


COMMANDS = {
    "run": cmd_run, "resume": cmd_resume, "cancel": cmd_cancel, "status": cmd_status,
    "recheck": cmd_recheck, "validate": cmd_validate, "preview": cmd_preview, "keys": cmd_keys,
}
