"""read_counter: read counter.txt.

Name, description and JSON schema are exactly what v1 sent the model."""

from testbed import ToolContext, tool

NO_ARGS = {"properties": {}, "required": [], "type": "object"}


@tool(file="counter.txt", op="read", schema=NO_ARGS)
def read_counter(ctx: ToolContext) -> str:
    """Read the UTF-8 contents of counter.txt."""
    return ctx.files.read("counter.txt")
