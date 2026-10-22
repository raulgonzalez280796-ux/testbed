"""Tools: every tool in the project, one Python file each (tools/<name>.py).

Review a tool's code and what the model sees, keep a note about it (for you
only, never sent to a model), edit, check, duplicate or delete it, and add new
ones from a starter, pasted code or an uploaded .py file. Code is checked
without running it (pyflakes and the testbed's rules), then imported in a
separate process, so a broken file cannot crash the UI.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from testbed.tools.check_code import tool_name_in
from testbed.tools.library import (
    STARTERS, LibraryError, check_name, delete_tool, duplicate_tool, load_notes, save_tool,
    set_note, starter_code, tool_files, used_by,
)
from testbed.tools.loader import has_errors, inspect_file
from testbed.ui.components.code_box import code_box
from testbed.ui.components.common import first_line, flash, home, inspect, show_flash

NEW = "__new__"
OPEN = "tools_open"
HOW = {"starter": "A starter", "paste": "Paste code", "upload": "Upload a .py file"}


def check_source(source: str, name: str) -> dict:
    """Check code as if it were tools/<name>.py (in a temporary folder)."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"{name}.py"
        path.write_text(source, encoding="utf-8")
        return inspect_file(path, kind="tools")


def status(report: dict) -> str:
    problems = report.get("problems", [])
    errors = sum(item["level"] == "error" for item in problems)
    warnings = len(problems) - errors
    if errors:
        return f"✗ {errors} error{'s' * (errors > 1)}"
    if warnings:
        return f"⚠ {warnings} warning{'s' * (warnings > 1)}"
    return "✓ ok"


def show_problems(report: dict) -> None:
    problems = report.get("problems", [])
    if not problems:
        st.success("No problems found: the code is valid Python and follows the tool rules.")
        return
    for item in problems:
        where = f"Line {item['line']}: " if item.get("line") else ""
        (st.error if item["level"] == "error" else st.warning)(f"{where}{item['message']}")
    if not has_errors(report):
        st.caption("Warnings don't stop you from saving.")


def file_text(item: dict) -> str:
    if item.get("file"):
        return f"{item['file']} ({item.get('op') or 'any action'})"
    return item.get("op") or ""


def used_text(uses: dict[str, list[str]]) -> str:
    return "; ".join(f"{Path(name).stem} ({', '.join(agents)})" for name, agents in uses.items())


def library_rows(project: Path, notes: dict) -> list[dict]:
    rows = []
    for path in tool_files(project / "tools"):
        report = inspect(path, "tools")
        tools = report.get("tools", [])
        only = tools[0] if len(tools) == 1 else {}
        rows.append({
            "Tool": path.stem,
            "Check": status(report),
            "Your note": notes.get(path.stem, ""),
            "File": file_text(only),
            "Used by": used_text(used_by(project / "scenarios", path.stem)),
            "The model is told": (first_line(only.get("description", "")) if only else
                                  f"{len(tools)} tools in one file: " + ", ".join(t["name"] for t in tools)
                                  if tools else ""),
        })
    return rows


COLUMNS = {
    "Tool": st.column_config.TextColumn(width=130),
    "Check": st.column_config.TextColumn(width=100),
    "Your note": st.column_config.TextColumn(width="large", help="Only you see this; edit it below"),
    "File": st.column_config.TextColumn(width=150, help="The one file the tool works on, if it declares one"),
    "Used by": st.column_config.TextColumn(width="medium", help="Scenarios, and the agents in them"),
    "The model is told": st.column_config.TextColumn(width="large", help="The first line of the description"),
}


def open_tool(name: str) -> None:
    """Open a tool on the next run (a widget's value can't change after it is drawn)."""
    st.session_state["tools_open_next"] = name


# -- one tool -----------------------------------------------------------------

def tool_panel(project: Path, name: str, notes: dict) -> None:
    tools_dir = project / "tools"
    path = tools_dir / f"{name}.py"
    report = inspect(path, "tools")
    tools = report.get("tools", [])
    st.subheader(f"{name} · {status(report)}")

    st.text_input(
        "Your note", notes.get(name, ""), key=f"tool_note_{name}",
        placeholder="What is this tool for? Only you see this; it is never sent to a model.",
        on_change=lambda: (set_note(tools_dir, name, st.session_state[f"tool_note_{name}"]),
                           flash(f"Saved the note for {name}.")))

    for item in tools:
        with st.container(border=True):
            st.markdown(f"**What the model sees:** `{item['name']}` — "
                        f"{item.get('description') or '(no description)'}")
            params = item["parameters"].get("properties", {})
            st.caption("Arguments the model fills in: " + (
                ", ".join(f"`{arg}` ({spec.get('type', 'any')})" for arg, spec in params.items()) or "none"))
            if item.get("file"):
                st.caption(f"Works only on `{item['file']}`" + (f", to {item['op']} it." if item.get("op") else "."))
            elif item.get("op"):
                st.caption(f"Action recorded in the log: {item['op']}.")
    uses = used_by(project / "scenarios", name)
    st.caption("Used by: " + (used_text(uses) if uses else "no scenario yet. Give it to an agent on the "
                                                               "Scenarios page, Agents tab."))

    stat = path.stat()
    text = code_box(f"tool_{name}_{stat.st_mtime_ns}", path.read_text(encoding="utf-8"))
    buttons = st.columns(4)
    checked = buttons[0].button("Check", key=f"tool_check_{name}", width="stretch",
                                help="Look for Python problems and tool-rule problems, without saving.")
    saving = buttons[1].button("Save", type="primary", key=f"tool_save_{name}", width="stretch")
    if checked or saving:
        result = check_source(text, name)
        if saving and not has_errors(result):
            save_tool(tools_dir, name, text)
            flash(f"Saved tools/{name}.py." + (" It has warnings: press Check to see them."
                                                if result["problems"] else ""))
            st.rerun()
        if saving:
            st.error("Not saved: fix the errors below first.")
        show_problems(result)
    elif report.get("problems"):
        with st.expander(f"Problems in the saved file ({status(report)})"):
            show_problems(report)

    with buttons[2].popover("Duplicate", width="stretch"):
        new_name = st.text_input("Name of the copy", f"{name}_copy", key=f"tool_dup_name_{name}",
                                 help="The file and the function get this name; the note is copied.")
        if st.button("Duplicate", type="primary", key=f"tool_dup_{name}"):
            try:
                duplicate_tool(tools_dir, name, new_name)
            except LibraryError as error:
                st.error(str(error))
            else:
                flash(f"Created {new_name.strip()} as a copy of {name}.")
                open_tool(new_name.strip())
                st.rerun()
    with buttons[3].popover("Delete", width="stretch"):
        if uses:
            st.warning(f"{used_text(uses)} use this tool. Those scenarios stop validating until "
                       "you take it off their agents.")
        st.write(f"Delete `tools/{name}.py` and its note?")
        if st.button("Delete", type="primary", key=f"tool_delete_{name}"):
            delete_tool(tools_dir, name)
            flash(f"Deleted tools/{name}.py.", "info")
            open_tool("")
            st.rerun()


# -- a new tool ---------------------------------------------------------------

def save_new(project: Path, source: str, note: str) -> None:
    """Check and save pasted or uploaded code; its tool name is the function's name."""
    name = tool_name_in(source)
    if name is None:
        result = check_source(source, "new_tool")
        st.error("Not saved: the code needs exactly one @tool function; its name becomes the tool's name.")
        show_problems(result)
        return
    try:
        check_name(name, project / "tools")
    except LibraryError as error:
        st.error(f"Not saved: {error} Rename the function, or open {name} to edit it.")
        return
    result = check_source(source, name)
    if has_errors(result):
        st.error("Not saved: fix the errors below first.")
        show_problems(result)
        return
    save_tool(project / "tools", name, source)
    if note.strip():
        set_note(project / "tools", name, note)
    flash(f"Added the tool {name} (tools/{name}.py).")
    open_tool(name)
    st.rerun()


def new_panel(project: Path) -> None:
    tools_dir = project / "tools"
    st.subheader("Add a tool")
    how = st.radio("Start from", list(HOW), format_func=HOW.get, horizontal=True, key="tool_new_how")
    if how == "starter":
        name = st.text_input("Name", key="tool_new_name", placeholder="for example read_notes",
                             help="The model calls the tool by this name; it is saved as tools/<name>.py.")
        kind = st.radio("Starter", list(STARTERS), format_func=STARTERS.get, horizontal=True,
                        key="tool_new_starter")
        file = ""
        if kind in ("read", "write", "append"):
            file = st.text_input("The file it works on", "notes.txt", key="tool_new_file",
                                 help="The tool can touch only this file. Add the file to a scenario on "
                                      "its Files tab.")
        note = st.text_input("Your note (optional)", key="tool_new_note")
        try:
            name = check_name(name, tools_dir) if name.strip() else ""
            code = starter_code(kind, name or "my_tool", file)
        except LibraryError as error:
            st.error(str(error))
            return
        st.code(code, language="python")
        if st.button("Create tool", type="primary", disabled=not name, key="tool_new_create",
                     help="You can edit the code after creating it."):
            save_tool(tools_dir, name, code)
            if note.strip():
                set_note(tools_dir, name, note)
            flash(f"Created the tool {name} (tools/{name}.py).")
            open_tool(name)
            st.rerun()
        return
    note = st.text_input("Your note (optional)", key="tool_new_note")
    if how == "paste":
        st.caption("One @tool function per tool. Its name becomes the tool's name and file name.")
        source = code_box("tool_new_paste", starter_code("blank", "my_tool"))
    else:
        upload = st.file_uploader("Python file", type=["py"], key="tool_new_upload")
        if upload is None:
            return
        source = upload.getvalue().decode("utf-8", errors="replace")
        st.code(source, language="python")
    name = tool_name_in(source)
    st.caption(f"Will be saved as `tools/{name}.py`." if name else
               "No single @tool function found yet.")
    left, right = st.columns(2)
    if left.button("Check", key="tool_new_check", width="stretch"):
        show_problems(check_source(source, name or "new_tool"))
    if right.button("Save", type="primary", key="tool_new_save", width="stretch"):
        save_new(project, source, note)


# -- page ---------------------------------------------------------------------

def render() -> None:
    project = home()
    tools_dir = project / "tools"
    st.title("Tools")
    show_flash()
    st.markdown(
        "Every tool is a Python file in `tools/`, holding one `@tool` function with the same name: "
        "`tools/read_log.py` is the tool **read_log**. The function's docstring and arguments are what "
        "the model sees. Give tools to agents on the Scenarios page (Agents tab). "
        "The ready-made tools (check_env, read_log, write_log, ...) are ordinary tools: edit or copy them.")
    st.caption(":material/shield: Tool code runs on this computer with your permissions, like any "
               "script you run. Only add code you wrote or have read.")

    notes = load_notes(tools_dir)
    rows = library_rows(project, notes)
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config=COLUMNS,
                     height=35 * (min(len(rows), 15) + 1) + 3)  # every row, up to 15, without scrolling
    else:
        st.info("No tools yet. Add one below, or run `python -m testbed init <project>` to get the "
                "ready-made tools.")

    names = [row["Tool"] for row in rows]
    pending = st.session_state.pop("tools_open_next", None)
    if pending is not None:
        st.session_state[OPEN] = pending if pending in names else (names[0] if names else NEW)
    if st.session_state.get(OPEN) not in [NEW, *names]:
        st.session_state[OPEN] = names[0] if names else NEW
    left, right = st.columns([3, 1], vertical_alignment="bottom")
    chosen = left.selectbox("Open a tool", [*names, NEW], key=OPEN,
                            format_func=lambda name: "+ Add a new tool" if name == NEW else name)
    right.button("Add tool", icon=":material/add:", width="stretch", key="tools_add",
                 on_click=lambda: st.session_state.update({OPEN: NEW}))
    st.divider()
    if chosen == NEW:
        new_panel(project)
    else:
        tool_panel(project, chosen, notes)
