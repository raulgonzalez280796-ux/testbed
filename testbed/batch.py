"""Batches: a frozen, self-contained folder that any executor can run or resume.

runs/<batch_id>/
  batch.json        settings: trials, parallel, seed, scenario hash
  scenario.yaml     the scenario as it was launched (frozen)
  code/tools, code/checks   the researcher code it uses (frozen)
  summary.json, summary.csv one row per trial (relative paths only)
  trial_0001/ ...   manifest.json, events.jsonl.gz, result.json, workspace/
  cancel            created to ask a running batch to stop
"""

from __future__ import annotations

import csv
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from ._version import __version__
from .scenario import Scenario, load_scenario, save_scenario, scenario_hash
from .tools.loader import resolve_agent_tools, resolve_check
from .trial import copy_code, now, write_json

FINAL_STATUSES = {"completed", "timeout", "infra_error", "budget_stopped", "cancelled"}


def new_batch_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + secrets.token_hex(4)


def validate_code(home: Path, scenario: Scenario) -> None:
    """Fail before any API call if a referenced tool or check cannot be loaded."""
    for agent in scenario.agents:
        resolve_agent_tools(agent.tools, home / "tools", agent.id)
    for ref in scenario.checks:
        resolve_check(ref, home / "checks")


def create_batch(
    home: Path, scenario_path: Path, *, trials: int, parallel: int,
    seed: int | None = None, label: str = "", projection: dict | None = None,
) -> Path:
    if trials < 1 or parallel < 1:
        raise ValueError("trials and parallel must be at least 1")
    scenario = load_scenario(scenario_path)
    validate_code(home, scenario)
    batch_id = new_batch_id()
    batch_dir = home / "runs" / batch_id
    batch_dir.mkdir(parents=True)
    copy_code(home, scenario, batch_dir)
    save_scenario(scenario, batch_dir / "scenario.yaml")
    try:
        source = str(Path(scenario_path).resolve().relative_to(home.resolve()))
    except ValueError:
        source = Path(scenario_path).name
    write_json(batch_dir / "batch.json", {
        "batch_id": batch_id, "label": label,
        "scenario_name": scenario.name, "scenario_file": source,
        "scenario_hash": scenario_hash(scenario),
        "trials": trials, "parallel": min(parallel, trials),
        "seed": seed if seed is not None else secrets.randbits(63),
        "created_at": now(), "testbed_version": __version__,
        "projection": projection,
    })
    return batch_dir


def load_batch(batch_dir: Path) -> tuple[dict, Scenario]:
    spec = json.loads((batch_dir / "batch.json").read_text(encoding="utf-8"))
    return spec, load_scenario(batch_dir / "scenario.yaml")


def trial_row(batch_dir: Path, index: int) -> dict:
    trial_dir = batch_dir / f"trial_{index:04d}"
    row = {"trial": index, "status": "queued", "stop_reason": None, "duration_seconds": None,
           "tokens": None, "error": None, "dir": trial_dir.name}
    result_path = trial_dir / "result.json"
    if result_path.exists():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return row
        row.update(
            status=result.get("status"), stop_reason=result.get("stop_reason"),
            duration_seconds=result.get("duration_seconds"),
            tokens=(result.get("tokens") or {}).get("total"),
            error=((result.get("error") or {}).get("kind")),
        )
        for ref, value in (result.get("checks") or {}).items():
            row[f"check:{ref}"] = value
    elif trial_dir.exists():
        row["status"] = "incomplete"
    return row


class Summary:
    """summary.json / summary.csv, rewritten as trials change state."""

    def __init__(self, batch_dir: Path, spec: dict, scenario: Scenario):
        self.batch_dir = batch_dir
        self.spec = spec
        self.scenario = scenario
        self.rows = {i: trial_row(batch_dir, i) for i in range(1, spec["trials"] + 1)}
        self.status = "created"
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.message: str | None = None
        self.projection: dict | None = spec.get("projection")
        self.running_peak = 0
        self.rate_limits: dict = {}

    def set(self, index: int, **fields) -> None:
        self.rows[index].update(fields)

    def refresh(self, index: int) -> None:
        self.rows[index] = trial_row(self.batch_dir, index)

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.rows.values():
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        return counts

    def save(self) -> None:
        rows = [self.rows[i] for i in sorted(self.rows)]
        tokens = sum(row["tokens"] or 0 for row in rows)
        write_json(self.batch_dir / "summary.json", {
            "batch_id": self.spec["batch_id"], "scenario_name": self.spec["scenario_name"],
            "status": self.status, "message": self.message,
            "planned_trials": self.spec["trials"], "parallel": self.spec["parallel"],
            "started_at": self.started_at, "finished_at": self.finished_at,
            "counts": self.counts(), "tokens_total": tokens,
            "budget": self.scenario.budget.model_dump(),
            "projection": self.projection, "running_peak": self.running_peak,
            "rate_limits": self.rate_limits,
            "trials": rows,
        })
        columns = ["trial", "status", "stop_reason", "duration_seconds", "tokens", "error", "dir"]
        columns += [f"check:{ref}" for ref in self.scenario.checks]
        tmp = self.batch_dir / "summary.csv.tmp"
        with tmp.open("w", newline="", encoding="utf-8") as out:
            writer = csv.DictWriter(out, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        tmp.replace(self.batch_dir / "summary.csv")


def read_summary(batch_dir: Path) -> dict | None:
    path = batch_dir / "summary.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def list_batches(home: Path) -> list[Path]:
    runs = home / "runs"
    if not runs.is_dir():
        return []
    return sorted(
        (path for path in runs.iterdir() if (path / "batch.json").exists()),
        key=lambda path: path.name, reverse=True,
    )


def request_cancel(batch_dir: Path) -> None:
    (batch_dir / "cancel").write_text(now(), encoding="utf-8")
