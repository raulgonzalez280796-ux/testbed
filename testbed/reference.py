"""The scenario reference (docs/scenario-reference.md), generated from testbed/scenario.py.

    python -m testbed reference --out docs/scenario-reference.md

A test checks that the file matches the code, so the docs cannot drift.
"""

from __future__ import annotations

import types
import typing
from typing import Any, Literal, get_args, get_origin

from annotated_types import Ge, Gt, Le, MinLen
from pydantic import BaseModel

from .scenario import (
    AgentSpec, BudgetSpec, FileSpec, LimitsSpec, Scenario, ScheduleSpec, VariableSpec,
)

SECTIONS: list[tuple[str, type[BaseModel], str]] = [
    ("Scenario", Scenario, "The top level of the YAML file."),
    ("Agent", AgentSpec, "Each item of `agents`."),
    ("File", FileSpec, "Each item of `files`."),
    ("Variable", VariableSpec, "Each value of `variables` (the key is the variable's name)."),
    ("Schedule", ScheduleSpec, "`schedule`."),
    ("Budget", BudgetSpec, "`budget`."),
    ("Limits", LimitsSpec, "`limits`."),
]
TITLES = {model: title for title, model, _ in SECTIONS}
SIMPLE = {str: "text", int: "integer", float: "number", bool: "true/false"}

EXAMPLE = """\
name: my-experiment
interaction: environment          # or: conversation
variables:
  token: {type: random_token, prefix: arena_}
files:
  - name: notes.txt
    content: "Token: {{token}}"
    access: {A: [read], B: [read, append]}
agents:
  - id: A
    provider: anthropic
    model: claude-haiku-5-5
    task_prompt: Read notes.txt and tell B what it says.
    continuation_prompt: Continue.
    turns: 3
    tools: [read_file]
  - id: B
    provider: openai
    model: gpt-6-luna
    task_prompt: Wait for information in notes.txt, then summarize it.
    continuation_prompt: Continue.
    turns: 3
    tools: [read_file, append_file]
checks: [my_checks:token_was_shared]
budget: {max_trial_tokens: 200000}
"""


def type_name(annotation: Any) -> str:
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is Literal:
        return "one of " + ", ".join(f"`{arg}`" for arg in args)
    if origin in (typing.Union, types.UnionType):
        parts = [type_name(arg) for arg in args if arg is not type(None)]
        return " or ".join(parts) + (", optional" if type(None) in args else "")
    if origin is list:
        return f"list of {type_name(args[0])}"
    if origin is dict:
        return f"map of {type_name(args[0])} → {type_name(args[1])}"
    if annotation is Any:
        return "any"
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        title = TITLES.get(annotation, annotation.__name__)
        return f"[{title}](#{title.lower()})"
    return SIMPLE.get(annotation, getattr(annotation, "__name__", str(annotation)))


def limits_text(metadata: list) -> str:
    parts = []
    for item in metadata:
        if isinstance(item, Ge):
            parts.append(f"≥ {item.ge}")
        elif isinstance(item, Gt):
            parts.append(f"> {item.gt}")
        elif isinstance(item, Le):
            parts.append(f"≤ {item.le}")
        elif isinstance(item, MinLen):
            parts.append(f"at least {item.min_length}")
    return ", ".join(parts)


def default_text(field) -> str:
    if field.is_required():
        return "**required**"
    value = field.get_default(call_default_factory=True)
    if value is None:
        return "empty"
    if isinstance(value, BaseModel):
        return "see below"
    if value == "" or value == [] or value == {}:
        return "empty"
    return f"`{value}`"


def table(model: type[BaseModel]) -> list[str]:
    rows = ["| Field | Type | Default | Description |", "|---|---|---|---|"]
    for name, field in model.model_fields.items():
        kind = type_name(field.annotation)
        bounds = limits_text(field.metadata)
        if bounds:
            kind += f" ({bounds})"
        description = (field.description or "").replace("|", "\\|")
        rows.append(f"| `{name}` | {kind} | {default_text(field)} | {description} |")
    return rows


def render_reference() -> str:
    lines = [
        "# Scenario reference",
        "",
        "<!-- Generated from testbed/scenario.py by `python -m testbed reference`. "
        "Do not edit by hand. -->",
        "",
        "A scenario is one YAML file in your project's `scenarios/` folder. The UI's scenario "
        "editor writes the same file. Unknown fields are rejected, so a typo is reported "
        "instead of ignored. For autocompletion in a code editor, export the JSON Schema with "
        "`python -m testbed schema --out scenario.schema.json`.",
        "",
        "A small example with two agents on different providers:",
        "",
        "```yaml",
        EXAMPLE.rstrip(),
        "```",
        "",
        "Rules checked when a scenario is loaded, beyond the field types below:",
        "",
        "- Agent ids and file names are unique.",
        "- `schedule.order`, if set, lists every agent exactly once.",
        "- `files[].access` names only existing agents, or `*`.",
        "- Every `{{variable}}` used in a prompt or file content is defined in `variables`.",
        "- In environment mode, every agent has a turn-1 input (`shared_task` or its "
        "`task_prompt`), and an agent with more than one turn has a `continuation_prompt`.",
        "- `conversation_format` contains `{text}`.",
        "",
    ]
    for title, model, where in SECTIONS:
        lines += [f"## {title}", "", f"{where} {(model.__doc__ or '').strip()}", ""]
        lines += table(model)
        lines.append("")
    return "\n".join(lines)


def scenario_json_schema() -> dict:
    schema = Scenario.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "llmArena testbed scenario"
    return schema
