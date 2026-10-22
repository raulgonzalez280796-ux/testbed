"""read_log: read log.txt.

Name, description and JSON schema are exactly what v1 sent the model."""

from testbed import ToolContext, tool

NO_ARGS = {"properties": {}, "required": [], "type": "object"}


@tool(file="log.txt", op="read", schema=NO_ARGS)
def read_log(ctx: ToolContext) -> str:
    """Read the UTF-8 contents of log.txt."""
    return ctx.files.read("log.txt")
