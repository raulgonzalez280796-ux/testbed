"""check_env: list every file in the workspace.

Name, description and JSON schema are exactly what v1 sent the model."""

from testbed import ToolContext, tool

NO_ARGS = {"properties": {}, "required": [], "type": "object"}


@tool(op="list", schema=NO_ARGS)
def check_env(ctx: ToolContext) -> list[str]:
    """List the names of files in the workspace."""
    return ctx.files.list()
