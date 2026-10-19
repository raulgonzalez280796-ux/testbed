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
