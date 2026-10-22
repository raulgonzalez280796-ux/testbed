"""write_counter: replace the contents of counter.txt.

Name, description and JSON schema are exactly what v1 sent the model."""

from testbed import ToolContext, tool

SCHEMA = {
    "properties": {"content": {"description": "content", "title": "Content", "type": "string"}},
    "required": ["content"],
    "type": "object",
}


@tool(file="counter.txt", op="write", schema=SCHEMA)
def write_counter(ctx: ToolContext, content: str) -> str:
    """Replace the contents of counter.txt with the supplied UTF-8 text."""
    ctx.files.write("counter.txt", content)
    return "File written."
