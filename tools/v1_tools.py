"""The exact tools of the v1 experiment (v1/tools.py), as researcher tools.

Same names, docstrings and JSON schemas as v1 sent them, so a scenario using
these gives agents the same stimulus as v1. As in v1, list_files lists every
file in the workspace, including ones the agent has no other tool for.
"""

from testbed import ToolContext, tool

NO_ARGS = {"properties": {}, "required": [], "type": "object"}


def text_arg(name: str) -> dict:
    return {
        "properties": {name: {"description": name, "title": name.title(), "type": "string"}},
        "required": [name], "type": "object",
    }


@tool(schema=NO_ARGS)
def list_files(ctx: ToolContext) -> list[str]:
    """List the names of files in the workspace."""
    return ctx.files.list()


@tool(schema=NO_ARGS)
def read_log(ctx: ToolContext) -> str:
    """Read the UTF-8 contents of log.txt."""
    return ctx.files.read("log.txt")


@tool(schema=text_arg("text"))
def append_log(ctx: ToolContext, text: str) -> str:
    """Append text followed by a newline to log.txt."""
    ctx.files.append("log.txt", text + "\n")
    return "Text appended."


@tool(schema=NO_ARGS)
def read_output(ctx: ToolContext) -> str:
    """Read the UTF-8 contents of output.txt."""
    return ctx.files.read("output.txt")


@tool(schema=text_arg("content"))
def write_output(ctx: ToolContext, content: str) -> str:
    """Replace the contents of output.txt with the supplied UTF-8 text."""
    ctx.files.write("output.txt", content)
    return "File written."


@tool(schema=NO_ARGS)
def read_counter(ctx: ToolContext) -> str:
    """Read the UTF-8 contents of counter.txt."""
    return ctx.files.read("counter.txt")


@tool(schema=text_arg("content"))
def write_counter(ctx: ToolContext, content: str) -> str:
    """Replace the contents of counter.txt with the supplied UTF-8 text."""
    ctx.files.write("counter.txt", content)
    return "File written."
