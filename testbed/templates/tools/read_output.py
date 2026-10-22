"""read_output: read output.txt.

Name, description and JSON schema are exactly what v1 sent the model."""

from testbed import ToolContext, tool

NO_ARGS = {"properties": {}, "required": [], "type": "object"}


@tool(file="output.txt", op="read", schema=NO_ARGS)
def read_output(ctx: ToolContext) -> str:
    """Read the UTF-8 contents of output.txt."""
    return ctx.files.read("output.txt")
