"""One trial: fresh workspace -> manifest -> engine under a timeout -> checks -> result.

A trial is a self-contained unit: (frozen batch folder, index) -> trial folder.
That is what lets later executors run trials on other machines.
"""

from __future__ import annotations

import asyncio
import json
import platform
import random
import shutil
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Callable

from . import providers
from ._version import __version__
from .checks import TrialRecord, run_checks
from .engine import AgentRuntime, Engine, TrialStopped
from .keys import redact
from .logger import EventLogger, compress_events, read_events
from .providers.base import ProviderAdapter, Usage
from .scenario import Scenario, draw_variables, render, scenario_hash, trial_seed, turn_one_text
from .tools.loader import file_sha256, resolve_agent_tools, resolve_check
from .tools.workspace import Workspace

PACKAGES = ("anthropic", "openai", "google-genai", "pydantic", "PyYAML")
CHECK_STATUSES = {"completed", "timeout"}


def package_versions() -> dict[str, str | None]:
    versions = {}
    for name in PACKAGES:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = None
    return versions


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


class AdapterPool:
    """One adapter (and HTTP connection pool) per provider/key/settings per process."""

    def __init__(self):
        self.adapters: dict[tuple, ProviderAdapter] = {}

    def get(self, provider: str, api_key: str, timeout: float, max_retries: int) -> ProviderAdapter:
        key = (provider, api_key, timeout, max_retries)
        if key not in self.adapters:
            cls = providers.adapter_class(provider)
            self.adapters[key] = cls(api_key, timeout=timeout, max_retries=max_retries)
        return self.adapters[key]

    async def close(self) -> None:
        for adapter in self.adapters.values():
            try:
                await adapter.aclose()
            except Exception:
                pass
        self.adapters.clear()


@dataclass
class BatchContext:
    batch_dir: Path
    batch_id: str
    scenario: Scenario
    seed: int
    keys: dict[str, str]
    adapters: AdapterPool = field(default_factory=AdapterPool)
    charge: Callable[[Usage], None] | None = None
    precheck: Callable[[int], None] | None = None
    cancelled: Callable[[], bool] = lambda: False
    limiter: object | None = None

    @property
    def tools_dir(self) -> Path:
        return self.batch_dir / "code" / "tools"

    @property
    def checks_dir(self) -> Path:
        return self.batch_dir / "code" / "checks"

    def trial_dir(self, index: int) -> Path:
        return self.batch_dir / f"trial_{index:04d}"


def prepare_trial_dir(path: Path) -> None:
    """Keep a previous incomplete attempt for inspection instead of deleting it."""
    if not path.exists():
        return
    attempt = 1
    while path.with_name(f"{path.name}.attempt{attempt}").exists():
        attempt += 1
    path.rename(path.with_name(f"{path.name}.attempt{attempt}"))


def is_complete(path: Path) -> bool:
    result = path / "result.json"
    if not result.exists():
        return False
    try:
        return json.loads(result.read_text(encoding="utf-8")).get("status") == "completed"
    except json.JSONDecodeError:
        return False


async def run_trial(batch: BatchContext, index: int) -> dict:
    scenario = batch.scenario
    trial_dir = batch.trial_dir(index)
    prepare_trial_dir(trial_dir)
    workspace_dir = trial_dir / "workspace"
    workspace_dir.mkdir(parents=True)
    seed = trial_seed(batch.seed, index)
    rng = random.Random(seed)
    values = draw_variables(scenario.variables, rng)
    trial_id = f"{batch.batch_id}/trial_{index:04d}"
    started_at, started = now(), time.monotonic()

    for spec in scenario.files:
        (workspace_dir / spec.name).write_text(render(spec.content, values), encoding="utf-8")
    workspace = Workspace(
        workspace_dir, max_file_bytes=scenario.limits.max_file_bytes,
        max_input_bytes=scenario.limits.max_tool_input_bytes,
    )

    agents: dict[str, AgentRuntime] = {}
    agent_manifest = []
    for spec in scenario.agents:
        tools = resolve_agent_tools(spec.tools, batch.tools_dir, spec.id)
        adapter = batch.adapters.get(
            spec.provider, batch.keys.get(spec.provider, ""),
            spec.request_timeout_seconds, spec.max_retries,
        )
        runtime = AgentRuntime(
            spec=spec, adapter=adapter, tools=tools,
            system=render(spec.system_prompt, values),
            turn_one=turn_one_text(scenario, spec, values),
            continuation=render(spec.continuation_prompt, values),
            file_access=scenario.file_access(spec.id),
        )
        agents[spec.id] = runtime
        agent_manifest.append({
            "id": spec.id, "provider": spec.provider, "model": spec.model,
            "params": spec.params, "turns": spec.turns,
            "max_model_calls_per_turn": spec.max_model_calls_per_turn,
            "request_timeout_seconds": spec.request_timeout_seconds,
            "max_retries": spec.max_retries,
            "system_prompt": runtime.system,
            "turn_one_input": runtime.turn_one,
            "continuation_prompt": runtime.continuation,
            "file_access": {name: sorted(ops) for name, ops in runtime.file_access.items()},
            "tools": [
                {"ref": t.ref, "name": t.name, "description": t.description,
                 "parameters": t.parameters,
                 "source_sha256": file_sha256(t.source) if t.source else None}
                for t in tools.values()
            ],
            "adapter": {"class": type(adapter).__name__, "sdk": adapter.sdk,
                        "sdk_version": package_versions().get(adapter.sdk)},
        })

    code_files = sorted((batch.batch_dir / "code").rglob("*.py")) if (batch.batch_dir / "code").exists() else []
    manifest = {
        "trial_id": trial_id, "batch_id": batch.batch_id, "index": index, "seed": seed,
        "started_at": started_at,
        "scenario_name": scenario.name, "scenario_hash": scenario_hash(scenario),
        "scenario": scenario.model_dump(mode="json"),
        "interaction": scenario.interaction,
        "variables": values,
        "initial_files": {name: workspace.read(name) for name in workspace.list()},
        "agents": agent_manifest,
        "rounds": scenario.rounds,
        "timeout_seconds": scenario.timeout_seconds,
        "limits": scenario.limits.model_dump(),
        "budget": scenario.budget.model_dump(),
        "code_sha256": {
            str(path.relative_to(batch.batch_dir)): file_sha256(path) for path in code_files
        },
        "testbed_version": __version__,
        "python": platform.python_version(),
        "platform": sys.platform,
        "packages": package_versions(),
    }
    write_json(trial_dir / "manifest.json", manifest)

    log = EventLogger(trial_dir / "events.jsonl", trial_id)
    log.emit("run_started", agents=[spec.id for spec in scenario.agents], seed=seed)
    engine = Engine(
        scenario, agents, workspace, values, rng, log, keys=batch.keys,
        charge=batch.charge, precheck=batch.precheck, cancelled=batch.cancelled,
        limiter=batch.limiter,
    )
    status, stop_reason, error = "completed", None, None
    try:
        async with asyncio.timeout(scenario.timeout_seconds):
            stop_reason = await engine.run()
    except TrialStopped as stopped:
        status, stop_reason, error = stopped.status, stopped.stop_reason, stopped.error
    except TimeoutError:
        status, stop_reason = "timeout", "timeout"
        log.emit("run_timeout", seconds=scenario.timeout_seconds)
    except asyncio.CancelledError:
        status, stop_reason = "cancelled", "cancelled"
        log.emit("run_cancelled")
    except Exception as unexpected:
        status, stop_reason = "infra_error", "runtime_error"
        message = redact(f"{type(unexpected).__name__}: {unexpected}", batch.keys)
        error = {"kind": "runtime", "type": type(unexpected).__name__, "message": message}
        log.emit("runtime_error", error_type=type(unexpected).__name__, message=message,
                 traceback=redact(traceback.format_exc(), batch.keys))

    result = {
        "trial_id": trial_id, "index": index, "seed": seed,
        "status": status, "stop_reason": stop_reason, "error": error,
        "started_at": started_at, "finished_at": now(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "rounds_completed": engine.rounds_completed,
        "turns": {agent_id: agent.turns_taken for agent_id, agent in agents.items()},
        "tokens": {
            **engine.usage.as_dict(),
            "per_agent": {agent_id: agent.usage.as_dict() for agent_id, agent in agents.items()},
        },
        "checks": {}, "check_errors": {},
    }
    log.emit("run_finished", status=status, stop_reason=stop_reason, tokens=engine.usage.total)
    log.close()

    if status in CHECK_STATUSES and scenario.checks:
        result["checks"], result["check_errors"] = evaluate_checks(batch, trial_dir)
    else:
        result["checks"] = {ref: None for ref in scenario.checks}
        if scenario.checks:
            result["checks_not_applicable"] = True
    write_json(trial_dir / "result.json", result)
    compress_events(trial_dir)
    return result


def evaluate_checks(batch: BatchContext, trial_dir: Path) -> tuple[dict, dict]:
    functions, errors = {}, {}
    for ref in batch.scenario.checks:
        try:
            functions[ref] = resolve_check(ref, batch.checks_dir)
        except Exception as error:
            errors[ref] = f"{type(error).__name__}: {error}"
    record = TrialRecord.load(trial_dir)
    values, run_errors = run_checks(functions, record)
    for ref in errors:
        values[ref] = None
    return values, {**errors, **run_errors}


def recheck_trial(batch: BatchContext, trial_dir: Path) -> dict:
    """Re-run the scenario's checks on a saved trial (no API calls)."""
    result_path = trial_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("status") not in CHECK_STATUSES:
        return result
    result["checks"], result["check_errors"] = evaluate_checks(batch, trial_dir)
    result["rechecked_at"] = now()
    write_json(result_path, result)
    return result


def copy_code(home: Path, scenario: Scenario, batch_dir: Path) -> None:
    """Freeze the researcher code a scenario uses into the batch folder."""
    from .tools.loader import referenced_files

    tool_refs = [ref for agent in scenario.agents for ref in agent.tools]
    for kind, refs in (("tools", tool_refs), ("checks", scenario.checks)):
        target = batch_dir / "code" / kind
        target.mkdir(parents=True, exist_ok=True)
        stems = referenced_files(refs) if kind == "tools" else {ref.split(":", 1)[0] for ref in refs}
        for stem in sorted(stems):
            source = home / kind / f"{stem}.py"
            if not source.is_file():
                raise FileNotFoundError(f"{kind}/{stem}.py not found (needed by the scenario)")
            shutil.copy2(source, target / source.name)


def load_trial_events(trial_dir: Path) -> list[dict]:
    return read_events(trial_dir)
