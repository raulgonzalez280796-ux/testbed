"""Built-in file tools. Each agent can only reach the files and operations the
scenario's access table grants it; list_files shows only those files.

Names and docstrings here are what models see: they are part of the stimulus.
"""

from __future__ import annotations

from .decorator import Tool, ToolContext, ToolError, make_tool
from .workspace import WorkspaceError


def _allowed(ctx: ToolContext, name: str, op: str) -> None:
    ops = ctx.file_access.get(name)
    if ops is None:
        raise ToolError(f"No accessible file named {name!r}.")
    if op not in ops:
        raise ToolError(f"You do not have permission to {op} {name!r}.")


def list_files(ctx: ToolContext) -> list[str]:
    """List the names of the files you can access in the workspace."""
    return [name for name in ctx.files.list() if name in ctx.file_access]


def read_file(ctx: ToolContext, name: str) -> str:
    """Read the UTF-8 contents of a file."""
    _allowed(ctx, name, "read")
    try:
        return ctx.files.read(name)
    except WorkspaceError as error:
        raise ToolError(str(error)) from None


def write_file(ctx: ToolContext, name: str, content: str) -> str:
    """Replace the contents of a file with the supplied UTF-8 text."""
    _allowed(ctx, name, "write")
    try:
        ctx.files.write(name, content)
    except WorkspaceError as error:
        raise ToolError(str(error)) from None
    return "File written."


def append_file(ctx: ToolContext, name: str, text: str) -> str:
    """Append text followed by a newline to a file."""
    _allowed(ctx, name, "append")
    try:
        ctx.files.append(name, text + "\n")
    except WorkspaceError as error:
        raise ToolError(str(error)) from None
    return "Text appended."


def _build() -> dict[str, Tool]:
    tools = {}
    for function in (list_files, read_file, write_file, append_file):
        built = make_tool(function)
        built.ref = built.name
        tools[built.name] = built
    return tools


BUILTIN_TOOLS: dict[str, Tool] = _build()


# -- single-purpose tools declared in a scenario (one action on one file) -------------

DEFAULT_DESCRIPTIONS = {
    "read_file": "Read the UTF-8 contents of {file}.",
    "write_file": "Replace the contents of {file} with the supplied UTF-8 text.",
    "append_file": "Append text followed by a newline to {file}.",
    "list_files": "List the names of files in the workspace.",
}
OPS = {"read_file": "read", "write_file": "write", "append_file": "append", "list_files": "list"}


def _workspace_call(action):
    try:
        return action()
    except WorkspaceError as error:
        raise ToolError(str(error)) from None


def make_bound_tool(definition) -> Tool:
    """A Tool for one scenario `tools:` entry. It takes no file name: the file is fixed."""
    kind, file = definition.kind, definition.file
    description = definition.description or DEFAULT_DESCRIPTIONS[kind].format(file=file)

    if kind == "read_file":
        def function(ctx: ToolContext) -> str:
            return _workspace_call(lambda: ctx.files.read(file))
    elif kind == "write_file":
        def function(ctx: ToolContext, content: str) -> str:
            _workspace_call(lambda: ctx.files.write(file, content))
            return "File written."
    elif kind == "append_file":
        def function(ctx: ToolContext, text: str) -> str:
            _workspace_call(lambda: ctx.files.append(file, text + "\n"))
            return "Text appended."
    else:
        shown = None if definition.files is None else set(definition.files)

        def function(ctx: ToolContext) -> list[str]:
            return [name for name in ctx.files.list() if shown is None or name in shown]

    built = make_tool(function, name=definition.name, description=description)
    built.ref = definition.name
    built.target_file = file
    built.op = OPS[kind]
    return built


def scenario_tools(scenario) -> dict[str, Tool]:
    """Name -> Tool for every entry in the scenario's `tools:` section."""
    return {definition.name: make_bound_tool(definition) for definition in scenario.tools}


def file_target(tool: Tool, arguments: dict) -> tuple[str | None, str | None]:
    """(file, op) a call acts on, when known: for single-purpose and generic file tools."""
    if getattr(tool, "op", None):
        return tool.target_file, tool.op
    generic = {"read_file": "read", "write_file": "write", "append_file": "append"}
    if tool.source is None and tool.name in generic:
        return arguments.get("name"), generic[tool.name]
    if tool.source is None and tool.name == "list_files":
        return None, "list"
    return None, None
