"""Scenario format: one YAML file is the source of truth for the UI, CLI and API.

Nothing here knows about any particular experiment. Every string that reaches a
model comes from the scenario file and is recorded in the trial manifest.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"
FILE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
TEMPLATE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
ALL_AGENTS = "*"

# "list" alone makes a file visible in list_files without reading or writing it.
FileOp = Literal["list", "read", "write", "append"]


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FileSpec(Spec):
    name: str = Field(pattern=FILE_NAME_PATTERN)
    content: str = ""
    # Agent id (or "*" for every agent) -> operations its built-in file tools may do.
    access: dict[str, list[FileOp]] = {}


class VariableSpec(Spec):
    type: Literal["constant", "random_token", "int_range", "choice"]
    value: Any = None            # constant
    prefix: str = ""             # random_token
    nbytes: int = Field(12, ge=1, le=64)  # random_token: hex characters = 2 * nbytes
    min: int | None = None       # int_range
    max: int | None = None
    options: list[Any] | None = None  # choice

    @model_validator(mode="after")
    def check_fields(self) -> VariableSpec:
        if self.type == "constant" and self.value is None:
            raise ValueError("a constant variable needs 'value'")
        if self.type == "int_range" and (self.min is None or self.max is None or self.min > self.max):
            raise ValueError("an int_range variable needs 'min' <= 'max'")
        if self.type == "choice" and not self.options:
            raise ValueError("a choice variable needs a non-empty 'options' list")
        return self


class AgentSpec(Spec):
    id: str = Field(pattern=NAME_PATTERN)
    provider: str
    model: str
    # Passed to the provider as-is (e.g. max_tokens, output_config, temperature).
    # The one testbed key is "cache" (default true): provider prompt caching.
    params: dict[str, Any] = {}
    system_prompt: str = ""
    task_prompt: str = ""          # turn 1, after the scenario's shared_task
    continuation_prompt: str = ""  # turns 2+
    turns: int = Field(10, ge=1, le=1000)
    max_model_calls_per_turn: int = Field(2, ge=1, le=100)
    # Built-in tool names, or "<file stem>:<function>" for researcher tools in tools/.
    tools: list[str] = []
    request_timeout_seconds: float = Field(60.0, gt=0)
    max_retries: int = Field(2, ge=0, le=10)


class ScheduleSpec(Spec):
    order: list[str] | None = None  # default: agents in the order they are listed
    mode: Literal["fixed", "random_each_round"] = "fixed"


class BudgetSpec(Spec):
    max_total_tokens: int | None = Field(None, ge=1)  # whole batch
    max_trial_tokens: int | None = Field(None, ge=1)  # one trial
    overrun_tolerance: float = Field(0.10, ge=0, le=1)


class LimitsSpec(Spec):
    max_tool_input_bytes: int = Field(16_000, ge=1)
    max_file_bytes: int = Field(64_000, ge=1)
    tool_timeout_seconds: float = Field(30.0, gt=0)


class Scenario(Spec):
    name: str = Field(min_length=1)
    description: str = ""
    interaction: Literal["environment", "conversation"] = "environment"
    # conversation mode: how other agents' messages are shown to an agent.
    conversation_format: str = "[{sender}]: {text}"
    schedule: ScheduleSpec = ScheduleSpec()
    max_rounds: int | None = Field(None, ge=1)  # default: the largest agent turn count
    timeout_seconds: float = Field(720.0, gt=0)
    shared_task: str = ""  # every agent gets this on turn 1, before its own task_prompt
    variables: dict[str, VariableSpec] = {}
    files: list[FileSpec] = []
    agents: list[AgentSpec] = Field(min_length=1)
    # "<file stem>:<function>" for researcher checks in checks/.
    checks: list[str] = []
    budget: BudgetSpec = BudgetSpec()
    limits: LimitsSpec = LimitsSpec()

    @field_validator("variables")
    @classmethod
    def check_variable_names(cls, value: dict[str, VariableSpec]) -> dict[str, VariableSpec]:
        for name in value:
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                raise ValueError(f"variable name {name!r} must be letters, digits and _")
        return value

    @model_validator(mode="after")
    def check_references(self) -> Scenario:
        ids = [agent.id for agent in self.agents]
        if len(set(ids)) != len(ids):
            raise ValueError(f"agent ids must be unique: {ids}")
        if self.schedule.order is not None:
            unknown = set(self.schedule.order) - set(ids)
            if unknown:
                raise ValueError(f"schedule.order names unknown agents: {sorted(unknown)}")
            if len(set(self.schedule.order)) != len(self.schedule.order):
                raise ValueError("schedule.order lists an agent twice")
        names = [spec.name for spec in self.files]
        if len(set(names)) != len(names):
            raise ValueError(f"file names must be unique: {names}")
        for spec in self.files:
            unknown = set(spec.access) - set(ids) - {ALL_AGENTS}
            if unknown:
                raise ValueError(f"file {spec.name!r}: access names unknown agents {sorted(unknown)}")
        for text, where in self.template_fields():
            for name in TEMPLATE.findall(text):
                if name not in self.variables:
                    raise ValueError(f"{where} uses {{{{{name}}}}}, which is not a defined variable")
        for agent in self.agents:
            if len(set(agent.tools)) != len(agent.tools):
                raise ValueError(f"agent {agent.id}: a tool is listed twice")
            if not (self.shared_task.strip() or agent.task_prompt.strip()):
                raise ValueError(
                    f"agent {agent.id} has no turn-1 input: set shared_task or its task_prompt"
                )
            if (
                self.interaction == "environment"
                and agent.turns > 1
                and not agent.continuation_prompt.strip()
            ):
                raise ValueError(
                    f"agent {agent.id} has {agent.turns} turns but no continuation_prompt "
                    "(required in environment mode)"
                )
        if "{text}" not in self.conversation_format:
            raise ValueError("conversation_format must contain {text}")
        return self

    def template_fields(self) -> list[tuple[str, str]]:
        fields = [(self.shared_task, "shared_task")]
        for agent in self.agents:
            for key in ("system_prompt", "task_prompt", "continuation_prompt"):
                fields.append((getattr(agent, key), f"agent {agent.id} {key}"))
        for spec in self.files:
            fields.append((spec.content, f"file {spec.name} content"))
        return fields

    def agent(self, agent_id: str) -> AgentSpec:
        return next(agent for agent in self.agents if agent.id == agent_id)

    @property
    def turn_order(self) -> list[str]:
        return self.schedule.order or [agent.id for agent in self.agents]

    @property
    def rounds(self) -> int:
        return self.max_rounds or max(agent.turns for agent in self.agents)

    @property
    def providers(self) -> set[str]:
        return {agent.provider for agent in self.agents}

    def file_access(self, agent_id: str) -> dict[str, set[str]]:
        """File name -> operations this agent's built-in file tools may do."""
        access = {}
        for spec in self.files:
            ops = set(spec.access.get(agent_id, [])) | set(spec.access.get(ALL_AGENTS, []))
            if ops:
                access[spec.name] = ops
        return access


def load_scenario(path: str | Path) -> Scenario:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a YAML mapping at the top level")
    return Scenario.model_validate(data)


def scenario_to_yaml(scenario: Scenario) -> str:
    data = scenario.model_dump(mode="json", exclude_defaults=True)
    data = {"name": scenario.name, **data}
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def save_scenario(scenario: Scenario, path: str | Path) -> None:
    Path(path).write_text(scenario_to_yaml(scenario), encoding="utf-8")


def scenario_hash(scenario: Scenario) -> str:
    canonical = json.dumps(scenario.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def trial_seed(batch_seed: int, index: int) -> int:
    digest = hashlib.sha256(f"{batch_seed}:{index}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def draw_variables(variables: dict[str, VariableSpec], rng: random.Random) -> dict[str, Any]:
    values = {}
    for name in sorted(variables):  # sorted: the draw order never depends on dict order
        spec = variables[name]
        if spec.type == "constant":
            values[name] = spec.value
        elif spec.type == "random_token":
            values[name] = spec.prefix + rng.getrandbits(8 * spec.nbytes).to_bytes(spec.nbytes, "big").hex()
        elif spec.type == "int_range":
            values[name] = rng.randint(spec.min, spec.max)
        elif spec.type == "choice":
            values[name] = rng.choice(spec.options)
    return values


def render(text: str, values: dict[str, Any]) -> str:
    return TEMPLATE.sub(lambda match: str(values[match.group(1)]), text)


def turn_one_text(scenario: Scenario, agent: AgentSpec, values: dict[str, Any]) -> str:
    parts = [render(scenario.shared_task, values), render(agent.task_prompt, values)]
    return "\n\n".join(part for part in parts if part.strip())
