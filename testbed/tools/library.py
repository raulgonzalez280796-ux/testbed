"""The project's tools library: tools/<name>.py, one tool per file, plus your notes.

The Tools page is built on these functions; nothing here imports Streamlit.
Notes live in tools/notes.yaml as {tool name: text}. They are for the
researcher only and are never sent to any model.
"""

from __future__ import annotations

import ast
import keyword
import re
from pathlib import Path

import yaml

from .workspace import NAME as FILE_NAME

NOTES_FILE = "notes.yaml"
TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
NOTES_HEADER = "# Your notes about each tool, shown on the Tools page. Never sent to any model.\n"


class LibraryError(Exception):
    pass


def check_name(name: str, tools_dir: Path, *, new: bool = True) -> str:
    """A valid name for a tool (and its file), or LibraryError saying why not."""
    name = name.strip()
    if not TOOL_NAME.match(name) or keyword.iskeyword(name):
        raise LibraryError(f"{name!r} can't be a tool name: use letters, digits and _, "
                           "starting with a letter (for example read_log).")
    if new and (tools_dir / f"{name}.py").exists():
        raise LibraryError(f"A tool called {name} already exists.")
    return name


def tool_files(tools_dir: Path) -> list[Path]:
    return sorted(path for path in tools_dir.glob("*.py") if not path.name.startswith("_"))


# --- notes -------------------------------------------------------------------

def load_notes(tools_dir: Path) -> dict[str, str]:
    try:
        data = yaml.safe_load((tools_dir / NOTES_FILE).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(name): str(text) for name, text in data.items() if text}


def save_notes(tools_dir: Path, notes: dict[str, str]) -> None:
    tools_dir.mkdir(parents=True, exist_ok=True)
    cleaned = {name: text.strip() for name, text in sorted(notes.items()) if text and text.strip()}
    body = yaml.safe_dump(cleaned, allow_unicode=True, sort_keys=False, width=1000) if cleaned else ""
    (tools_dir / NOTES_FILE).write_text(NOTES_HEADER + body, encoding="utf-8")


def set_note(tools_dir: Path, name: str, text: str) -> None:
    notes = load_notes(tools_dir)
    notes[name] = text
    save_notes(tools_dir, notes)


def merge_notes(tools_dir: Path, new: dict[str, str]) -> None:
    """Add notes for tools that have none; existing notes are never changed."""
    notes = load_notes(tools_dir)
    missing = {name: text for name, text in new.items() if name not in notes}
    if missing:
        save_notes(tools_dir, {**notes, **missing})


# --- who uses a tool ---------------------------------------------------------

def used_by(scenarios_dir: Path, name: str) -> dict[str, list[str]]:
    """Scenario file name -> the agents whose tool list names this tool.

    Read straight from the YAML, so a scenario that doesn't validate still counts.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(scenarios_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        agents = data.get("agents") if isinstance(data, dict) else None
        for agent in agents if isinstance(agents, list) else []:
            if not isinstance(agent, dict):
                continue
            refs = agent.get("tools") or []
            if any(str(ref) == name or str(ref).startswith(f"{name}:") for ref in refs):
                found.setdefault(path.name, []).append(str(agent.get("id", "?")))
    return found


# --- starters ----------------------------------------------------------------

STARTERS = {
    "read": "Read a file",
    "write": "Write a file (replaces it)",
    "append": "Append to a file",
    "list": "List the files",
    "blank": "Blank",
}

_HEADER = "from testbed import ToolContext, tool\n\n\n"


def starter_code(kind: str, name: str, file: str = "") -> str:
    """Ready-to-save code for a new tool. read/write/append need a file name."""
    if kind in ("read", "write", "append") and not FILE_NAME.match(file or ""):
        raise LibraryError("Give the file this tool works on, for example notes.txt.")
    if kind == "read":
        return _HEADER + f'''@tool(file="{file}", op="read")
def {name}(ctx: ToolContext) -> str:
    """Read the contents of {file}."""
    return ctx.files.read("{file}")
'''
    if kind == "write":
        return _HEADER + f'''@tool(file="{file}", op="write")
def {name}(ctx: ToolContext, content: str) -> str:
    """Replace the contents of {file} with the given text."""
    ctx.files.write("{file}", content)
    return "File written."
'''
    if kind == "append":
        return _HEADER + f'''@tool(file="{file}", op="append")
def {name}(ctx: ToolContext, text: str) -> str:
    """Add a line of text to the end of {file}."""
    ctx.files.append("{file}", text + "\\n")
    return "Text appended."
'''
    if kind == "list":
        return _HEADER + f'''@tool(op="list")
def {name}(ctx: ToolContext) -> list[str]:
    """List the names of the files in the workspace."""
    return ctx.files.list()
'''
    if kind == "blank":
        return f'''from typing import Annotated

from testbed import ToolContext, ToolError, tool


@tool
def {name}(ctx: ToolContext, text: Annotated[str, "What the model passes in."]) -> str:
    """Describe what the tool does. The model sees this description."""
    # ctx.agent_id, ctx.files.read/write/append/list, ctx.state (shared dict),
    # ctx.variables, ctx.rng, ctx.note(...), ctx.stop_trial(reason), ctx.finish_agent()
    if not text:
        raise ToolError("Give some text.")  # the model gets this message as the tool's error
    return "Done."
'''
    raise LibraryError(f"unknown starter {kind!r}")


# --- copy, rename, delete ----------------------------------------------------

def rename_function(source: str, old: str, new: str) -> str:
    """The source with the def of function `old` renamed to `new` (that line only)."""
    lines = source.splitlines(keepends=True)
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == old:
            index = node.lineno - 1
            lines[index] = re.sub(rf"\bdef\s+{re.escape(old)}\b", f"def {new}", lines[index], count=1)
            return "".join(lines)
    raise LibraryError(f"no function called {old} in this file")


def save_tool(tools_dir: Path, name: str, source: str) -> Path:
    tools_dir.mkdir(parents=True, exist_ok=True)
    path = tools_dir / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    return path


def duplicate_tool(tools_dir: Path, name: str, new_name: str) -> Path:
    """Copy tools/<name>.py to tools/<new_name>.py, with the function renamed. The note is copied."""
    new_name = check_name(new_name, tools_dir)
    source = (tools_dir / f"{name}.py").read_text(encoding="utf-8")
    try:
        source = rename_function(source, name, new_name)
    except (LibraryError, SyntaxError):
        pass  # not a one-tool file named like its function: copy it as it is
    path = save_tool(tools_dir, new_name, source)
    note = load_notes(tools_dir).get(name)
    if note:
        set_note(tools_dir, new_name, note)
    return path


def delete_tool(tools_dir: Path, name: str) -> None:
    (tools_dir / f"{name}.py").unlink()
    notes = load_notes(tools_dir)
    if notes.pop(name, None) is not None:
        save_notes(tools_dir, notes)
