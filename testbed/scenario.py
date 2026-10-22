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
    """A file in the workspace. Every trial starts with a fresh copy of each file."""

    name: str = Field(
        pattern=FILE_NAME_PATTERN,
        description="File name, without folders: letters, digits, '.', '_' and '-'.")
    content: str = Field(
        "", description="Initial content. {{variable}} is replaced with the trial's value.")
    access: dict[str, list[FileOp]] = Field(
        {}, description=(
            "What each agent's built-in file tools may do with this file: agent id (or '*' "
            "for every agent) -> a list of list, read, write, append. 'list' alone shows the "
            "name in list_files without allowing reads or writes. Researcher tools reach "
            "every file through ctx.files regardless."))


class VariableSpec(Spec):
    """A value drawn for each trial from the trial's seed, so a re-run trial gets the same value."""

    type: Literal["constant", "random_token", "int_range", "choice"] = Field(
        description="How the value is chosen.")
    value: Any = Field(None, description="constant: the value.")
    prefix: str = Field("", description="random_token: text before the random hex part.")
    nbytes: int = Field(
        12, ge=1, le=64,
        description="random_token: random bytes; the token has 2 hex characters per byte.")
    min: int | None = Field(None, description="int_range: the smallest value (inclusive).")
    max: int | None = Field(None, description="int_range: the largest value (inclusive).")
    options: list[Any] | None = Field(None, description="choice: the values to pick from.")

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
    """One agent: its model, prompts, turns and tools. Each agent keeps its own history."""

    id: str = Field(
        pattern=NAME_PATTERN,
        description=("Unique name: a letter, then letters, digits, '_' or '-'. In conversation "
                     "mode it is shown to the other agents as {sender}."))
    provider: str = Field(description="anthropic, openai or gemini.")
    model: str = Field(description="The provider's model id, e.g. claude-haiku-5-5.")
    params: dict[str, Any] = Field(
        {}, description=(
            "Sent to the provider as-is, e.g. max_tokens (all providers), output_config "
            "(Anthropic), reasoning (OpenAI), thinking_config (Gemini). The one testbed key "
            "is cache (default true): provider prompt caching."))
    system_prompt: str = Field("", description="Sent with every model call.")
    task_prompt: str = Field(
        "", description="The agent's turn-1 input, after the scenario's shared_task.")
    continuation_prompt: str = Field(
        "", description=("The input for turns 2 and later. Required in environment mode; in "
                         "conversation mode it may be empty, so the other agents' new "
                         "messages are the whole input."))
    turns: int = Field(
        10, ge=1, le=1000,
        description="How many turns this agent takes; it is skipped in later rounds.")
    max_model_calls_per_turn: int = Field(
        2, ge=1, le=100,
        description=("A turn ends when the model answers without a tool call, or after this "
                     "many calls. Tool results from the last call are seen next turn."))
    tools: list[str] = Field(
        [], description=("Built-in tool names (list_files, read_file, write_file, append_file), "
                         "or file:function for researcher tools in tools/."))
    request_timeout_seconds: float = Field(
        60.0, gt=0, description="Timeout for one model request.")
    max_retries: int = Field(
        2, ge=0, le=10, description="Quick retries inside the provider's SDK, per request.")


class ScheduleSpec(Spec):
    """Who takes turns, in what order."""

    order: list[str] | None = Field(
        None, description=("Turn order in each round; must list every agent once. Empty means "
                           "the order the agents are listed in."))
    mode: Literal["fixed", "random_each_round"] = Field(
        "fixed", description=("fixed: the same order every round. random_each_round: "
                              "shuffled every round from the trial's seed."))


class BudgetSpec(Spec):
    """Token caps. Token use is always recorded, with or without caps."""

    max_total_tokens: int | None = Field(
        None, ge=1, description=("Cap for the whole batch: no new trials start once it is "
                                 "reached; running trials stop at the cap plus the tolerance "
                                 "(status budget_stopped)."))
    max_trial_tokens: int | None = Field(
        None, ge=1, description="Cap for one trial: it ends with stop reason token_cap.")
    overrun_tolerance: float = Field(
        0.10, ge=0, le=1,
        description="How far running trials may go past max_total_tokens (0.10 = 10%).")


class LimitsSpec(Spec):
    """Safety limits for tools and retries."""

    max_tool_input_bytes: int = Field(
        16_000, ge=1, description="Largest tool-call input; larger calls return an error.")
    max_file_bytes: int = Field(
        64_000, ge=1, description="Largest workspace file the file tools will read or write.")
    tool_timeout_seconds: float = Field(
        30.0, gt=0, description="A tool call that takes longer returns a timeout error.")
    retry_attempts: int = Field(
        6, ge=0, le=20,
        description=("Retries after rate limits, server or connection errors, on top of the "
                     "SDK's own; the trial fails as infra_error after the last one."))
    retry_max_delay_seconds: float = Field(
        60.0, gt=0,
        description="Longest wait between retries when the provider gives no retry-after.")


class Scenario(Spec):
    """A complete experiment setup: one YAML file in scenarios/."""

    name: str = Field(min_length=1, description="Shown in the UI and recorded in every trial.")
    description: str = Field("", description="For people; never sent to any agent.")
    interaction: Literal["environment", "conversation"] = Field(
        "environment", description=(
            "environment: agents affect each other only through files and tools. "
            "conversation: each turn also starts with the other agents' final texts since "
            "this agent's last turn."))
    conversation_format: str = Field(
        "[{sender}]: {text}",
        description=("conversation mode: how each message from another agent is shown. "
                     "{sender} is its agent id, {text} the message; use {text} alone to hide "
                     "the sender."))
    schedule: ScheduleSpec = Field(ScheduleSpec(), description="Turn order.")
    max_rounds: int | None = Field(
        None, ge=1,
        description="Rounds per trial. Empty means the largest agent turn count.")
    timeout_seconds: float = Field(
        720.0, gt=0, description="A trial still running after this long ends as timeout.")
    shared_task: str = Field(
        "", description="Every agent gets this on turn 1, before its own task_prompt.")
    variables: dict[str, VariableSpec] = Field(
        {}, description="Per-trial values, inserted with {{name}} in prompts and file contents.")
    files: list[FileSpec] = Field([], description="The workspace files every trial starts with.")
    agents: list[AgentSpec] = Field(min_length=1, description="At least one agent.")
    checks: list[str] = Field(
        [], description="file:function for researcher checks in checks/, run after every trial.")
    budget: BudgetSpec = Field(BudgetSpec(), description="Token caps.")
    limits: LimitsSpec = Field(LimitsSpec(), description="Tool and retry limits.")

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
            missing = [agent_id for agent_id in ids if agent_id not in self.schedule.order]
            if missing:
                raise ValueError(f"schedule.order leaves out {missing}; they would never take a turn")
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
            if self.interaction == "environment" and not (
                self.shared_task.strip() or agent.task_prompt.strip()
            ):
                # In conversation mode the first input may be the other agents' messages.
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
