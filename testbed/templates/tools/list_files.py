"""list_files: list every file in the workspace.

Name, description and JSON schema are exactly what v1 sent the model."""

from testbed import ToolContext, tool

NO_ARGS = {"properties": {}, "required": [], "type": "object"}


@tool(op="list", schema=NO_ARGS)
def list_files(ctx: ToolContext) -> list[str]:
    """List the names of files in the workspace."""
    return ctx.files.list()
