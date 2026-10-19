"""Researcher-written checks: pure functions over a saved trial.

    from testbed import check, TrialRecord

    @check
    def output_matches_token(trial: TrialRecord) -> bool:
        return trial.files.get("output.txt") == trial.variables["token"]

Checks run after each trial and can be re-run later on saved trials at no API cost.
"""

from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .logger import read_events

CHECK_ATTR = "__testbed_check__"


def check(func: Callable | None = None):
    def mark(function: Callable) -> Callable:
        setattr(function, CHECK_ATTR, True)
        return function

    return mark(func) if func is not None else mark


@dataclass
class TrialRecord:
    trial_dir: Path
    files: dict[str, str]          # final workspace contents
    variables: dict[str, Any]
    events: list[dict]
    manifest: dict = field(default_factory=dict)
    status: str = "completed"

    @classmethod
    def load(cls, trial_dir: Path) -> TrialRecord:
        manifest = json.loads((trial_dir / "manifest.json").read_text(encoding="utf-8"))
        result_path = trial_dir / "result.json"
        status = "completed"
        if result_path.exists():
            status = json.loads(result_path.read_text(encoding="utf-8")).get("status", status)
        workspace = trial_dir / "workspace"
        files = {}
        if workspace.is_dir():
            for path in sorted(workspace.iterdir()):
                if path.is_file() and not path.is_symlink():
                    try:
                        files[path.name] = path.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        files[path.name] = "<binary>"
        return cls(
            trial_dir=trial_dir, files=files, variables=manifest.get("variables", {}),
            events=read_events(trial_dir), manifest=manifest, status=status,
        )

    def tool_calls(self, agent: str | None = None, tool: str | None = None) -> list[dict]:
        """Tool calls in order: {agent, tool, arguments, ok, result, round, seq}."""
        calls, pending = [], {}
        for event in self.events:
            data = event["data"]
            if event["event_type"] == "tool_started":
                pending[data["call_id"]] = {
                    "agent": data["agent_id"], "tool": data["tool"],
                    "arguments": data.get("arguments", {}), "round": data.get("round"),
                    "seq": event["seq"], "ok": None, "result": None,
                }
                calls.append(pending[data["call_id"]])
            elif event["event_type"] in ("tool_completed", "tool_failed"):
                entry = pending.get(data["call_id"])
                if entry is not None:
                    entry["ok"] = event["event_type"] == "tool_completed"
                    entry["result"] = data.get("result", data.get("error"))
        return [
            call for call in calls
            if (agent is None or call["agent"] == agent) and (tool is None or call["tool"] == tool)
        ]

    def messages(self, agent: str | None = None) -> list[dict]:
        """Each agent's final text per turn: {agent, round, text}."""
        return [
            {"agent": event["data"]["agent_id"], "round": event["data"]["round"],
             "text": event["data"].get("text", "")}
            for event in self.events
            if event["event_type"] == "turn_finished"
            and (agent is None or event["data"]["agent_id"] == agent)
        ]

    def events_of(self, event_type: str) -> list[dict]:
        return [event for event in self.events if event["event_type"] == event_type]


def run_checks(checks: dict[str, Callable], record: TrialRecord) -> tuple[dict, dict]:
    """Returns (values, errors). Values are JSON-safe; a failing check gets an error, not a value."""
    values, errors = {}, {}
    for ref, function in checks.items():
        try:
            value = function(record)
            json.dumps(value)
            values[ref] = value
        except Exception as error:
            values[ref] = None
            errors[ref] = "".join(traceback.format_exception_only(type(error), error)).strip()
    return values, errors
