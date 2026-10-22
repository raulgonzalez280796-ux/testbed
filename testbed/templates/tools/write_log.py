"""write_log: add a line to log.txt; earlier lines are kept.

Name, description and JSON schema are exactly what v1 sent the model."""

from testbed import ToolContext, tool

SCHEMA = {
    "properties": {"text": {"description": "text", "title": "Text", "type": "string"}},
    "required": ["text"],
    "type": "object",
}


@tool(file="log.txt", op="append", schema=SCHEMA)
def write_log(ctx: ToolContext, text: str) -> str:
    """Append text followed by a newline to log.txt."""
    ctx.files.append("log.txt", text + "\n")
    return "Text appended."
