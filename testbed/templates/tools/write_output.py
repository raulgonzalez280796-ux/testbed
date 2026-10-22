"""write_output: replace the contents of output.txt.

Name, description and JSON schema are exactly what v1 sent the model."""

from testbed import ToolContext, tool

SCHEMA = {
    "properties": {"content": {"description": "content", "title": "Content", "type": "string"}},
    "required": ["content"],
    "type": "object",
}


@tool(file="output.txt", op="write", schema=SCHEMA)
def write_output(ctx: ToolContext, content: str) -> str:
    """Replace the contents of output.txt with the supplied UTF-8 text."""
    ctx.files.write("output.txt", content)
    return "File written."
