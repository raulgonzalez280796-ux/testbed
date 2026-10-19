"""@tool decorator, ToolContext and tool invocation.

A researcher tool is a plain Python function. Its type hints become the JSON
schema the model sees, and its docstring becomes the description. An optional
first parameter `ctx: ToolContext` is injected by the runtime and never shown
to the model.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Callable, get_args, get_origin, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from ..providers.base import ToolSpec, tool_output_text
from .workspace import Workspace

TOOL_ATTR = "__testbed_tool__"


class ToolError(Exception):
    """Raise from a tool to return a clean error message to the model."""


class TrialControl:
    """Requests a tool can make of the engine; checked after each tool call."""

    def __init__(self):
        self.stop_reason: str | None = None
        self.finished_agents: set[str] = set()


@dataclass
class ToolContext:
    agent_id: str
    files: Workspace                 # every workspace file, without per-agent limits
    file_access: dict[str, set[str]]  # what this agent's built-in file tools may do
    state: dict                      # shared by all tools and agents in one trial
    variables: dict
    rng: random.Random               # seeded per trial
    round: int
    turn: int
    _note: Callable[..., None] = field(repr=False, default=lambda *a, **k: None)
    _control: TrialControl = field(repr=False, default_factory=TrialControl)

    def note(self, message: str, **data) -> None:
        """Write a `tool_note` event to the trial log (not shown to any agent)."""
        self._note(message, **data)

    def stop_trial(self, reason: str = "stopped_by_tool") -> None:
        """End the trial after this tool call returns."""
        self._control.stop_reason = reason

    def finish_agent(self) -> None:
        """This agent takes no more turns (its effects on the workspace stay)."""
        self._control.finished_agents.add(self.agent_id)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable
    takes_ctx: bool
    is_async: bool
    arg_model: type[BaseModel]
    ref: str = ""                    # how scenarios name it: "list_files" or "stem:function"
    source: Path | None = None       # None for built-ins

    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, parameters=self.parameters)

    async def invoke(self, ctx: ToolContext, arguments: dict, timeout: float) -> str:
        try:
            parsed = self.arg_model.model_validate(arguments)
        except ValidationError as error:
            raise ToolError(f"Invalid arguments: {error.errors(include_url=False)}") from None
        kwargs = {name: getattr(parsed, name) for name in self.arg_model.model_fields}
        args = (ctx,) if self.takes_ctx else ()
        if self.is_async:
            result = await asyncio.wait_for(self.func(*args, **kwargs), timeout)
        else:
            # A slow sync tool must not block the other trials in this process.
            result = await asyncio.wait_for(asyncio.to_thread(self.func, *args, **kwargs), timeout)
        return tool_output_text(result)


def tool(
    func: Callable | None = None, *, name: str | None = None,
    description: str | None = None, schema: dict | None = None,
):
    """Mark a function as a tool. Use as @tool or @tool(name=..., description=..., schema=...).

    `schema` replaces the generated JSON schema shown to the model (arguments
    are still validated against the type hints).
    """

    def mark(function: Callable) -> Callable:
        made = make_tool(function, name=name, description=description, schema=schema)
        setattr(function, TOOL_ATTR, made)
        return function

    return mark(func) if func is not None else mark


def make_tool(
    function: Callable, *, name: str | None = None,
    description: str | None = None, schema: dict | None = None,
) -> Tool:
    tool_name = name or function.__name__
    if not tool_name.replace("_", "").isalnum() or not tool_name[0].isalpha():
        raise ValueError(f"tool name {tool_name!r} must start with a letter and use letters, digits, _")
    takes_ctx, arg_model = argument_model(function, tool_name)
    return Tool(
        name=tool_name,
        description=description or inspect.getdoc(function) or "",
        parameters=schema if schema is not None else clean_schema(arg_model.model_json_schema()),
        func=function,
        takes_ctx=takes_ctx,
        is_async=inspect.iscoroutinefunction(function),
        arg_model=arg_model,
    )


def argument_model(function: Callable, tool_name: str) -> tuple[bool, type[BaseModel]]:
    signature = inspect.signature(function)
    hints = get_type_hints(function, include_extras=True)
    params = list(signature.parameters.values())
    takes_ctx = bool(params) and (
        params[0].name == "ctx" or _strip_annotated(hints.get(params[0].name)) is ToolContext
    )
    fields: dict[str, Any] = {}
    for param in params[1 if takes_ctx else 0:]:
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            raise TypeError(f"tool {tool_name}: *args and **kwargs are not supported")
        if param.name not in hints:
            raise TypeError(f"tool {tool_name}: parameter {param.name!r} needs a type hint")
        annotation = _plain_string_descriptions(hints[param.name])
        default = ... if param.default is param.empty else param.default
        fields[param.name] = (annotation, default)
    model = create_model(
        f"{tool_name}_arguments", __config__=ConfigDict(extra="forbid"), **fields,
    )
    return takes_ctx, model


def _strip_annotated(annotation):
    if get_origin(annotation) is Annotated:
        return get_args(annotation)[0]
    return annotation


def _plain_string_descriptions(annotation):
    """Allow Annotated[str, "description"] as shorthand for Field(description=...)."""
    if get_origin(annotation) is not Annotated:
        return annotation
    base, *extras = get_args(annotation)
    converted = [Field(description=item) if isinstance(item, str) else item for item in extras]
    return Annotated[(base, *converted)]


def clean_schema(schema: dict) -> dict:
    """Drop pydantic's auto-generated titles: they add tokens and carry no meaning."""

    def walk(node, in_properties=False):
        if isinstance(node, dict):
            result = {}
            for key, value in node.items():
                if key == "title" and not in_properties:
                    continue
                result[key] = walk(value, in_properties=(key in ("properties", "$defs")))
            return result
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    cleaned = walk(schema)
    cleaned.setdefault("type", "object")
    cleaned.setdefault("properties", {})
    return cleaned


def schema_json(tool_obj: Tool) -> str:
    return json.dumps(tool_obj.parameters, ensure_ascii=False, sort_keys=True)
