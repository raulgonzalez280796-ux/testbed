"""Tools & checks: add researcher code by pasting it or uploading a .py file.

Code is validated in a separate process before it is saved; a broken file
cannot crash the UI. It is saved as tools/<name>.py or checks/<name>.py.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

from testbed.project import safe_stem
from testbed.tools.loader import inspect_file
from testbed.ui.components.code_box import code_box
from testbed.ui.components.common import flash, home, inspect, show_flash

TOOL_TEMPLATE = '''from typing import Annotated

from testbed import ToolContext, ToolError, tool


@tool
def my_tool(ctx: ToolContext, text: Annotated[str, "What to write."]) -> str:
    """Describe what the tool does. The model sees this description."""
    # ctx.agent_id, ctx.files.read/write/append/list, ctx.state (shared dict),
    # ctx.variables, ctx.rng, ctx.note(...), ctx.stop_trial(reason), ctx.finish_agent()
    ctx.files.append("notes.txt", text + "\\n")
    return "Done."
'''

CHECK_TEMPLATE = '''from testbed import TrialRecord, check


@check
def my_check(trial: TrialRecord) -> bool:
    """Describe what this measures. Return a bool, number or string."""
    # trial.files (final contents), trial.variables, trial.tool_calls(agent=, tool=),
    # trial.messages(agent=), trial.events
    return "done" in trial.files.get("notes.txt", "")
'''

KINDS = {
    "tools": {"title": "Tools", "template": TOOL_TEMPLATE, "item": "tools"},
    "checks": {"title": "Checks", "template": CHECK_TEMPLATE, "item": "checks"},
}


def validate_source(source: str, stem: str) -> dict:
    """Inspect code in a temporary file with the same stem, so references come out right."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"{stem}.py"
        path.write_text(source, encoding="utf-8")
        return inspect_file(path)


def show_report(report: dict, kind: str) -> None:
    if not report.get("ok"):
        line = f" (line {report['line']})" if report.get("line") else ""
        st.error(f"{report.get('error', 'Could not load the file')}{line}")
        return
    items = report.get(kind, [])
    if not items:
        st.warning(f"No @{'tool' if kind == 'tools' else 'check'} functions found in this file.")
    for item in items:
        st.markdown(f"**`{item['ref']}`** — {item.get('description') or '(no description)'}")
        if kind == "tools":
            params = item["parameters"].get("properties", {})
            st.caption("Arguments the model fills in: " + (
                ", ".join(f"`{name}` ({spec.get('type', 'any')})" for name, spec in params.items())
                or "none"))


def save_code(project: Path, kind: str, stem: str, source: str) -> bool:
    report = validate_source(source, stem)
    if not report.get("ok") or not report.get(kind):
        show_report(report, kind)
        return False
    target = project / kind / f"{stem}.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    flash(f"Saved {kind}/{target.name}: " + ", ".join(item["name"] for item in report[kind]))
    return True


def editor(project: Path, kind: str) -> None:
    spec = KINDS[kind]
    files = sorted((project / kind).glob("*.py"))
    choices = ["(new file)"] + [path.name for path in files]
    chosen = st.selectbox("File", choices, key=f"{kind}_file")
    existing = None if chosen == "(new file)" else project / kind / chosen
    how = "Paste or write code"
    if existing is None:
        how = st.radio("Add by", ["Paste or write code", "Upload a .py file"], horizontal=True,
                       key=f"{kind}_how")
    if how == "Upload a .py file":
        upload = st.file_uploader("Python file", type=["py"], key=f"{kind}_upload")
        if upload is not None:
            stem = st.text_input("Save as", safe_stem(Path(upload.name).stem), key=f"{kind}_upload_stem")
            source = upload.getvalue().decode("utf-8", errors="replace")
            with st.expander("File contents"):
                st.code(source, language="python")
            if st.button("Validate and save", type="primary", key=f"{kind}_upload_save"):
                if save_code(project, kind, safe_stem(stem), source):
                    st.rerun()
        return
    stem = existing.stem if existing else st.text_input(
        "File name (letters, digits and _)", f"my_{spec['item'][:-1]}", key=f"{kind}_new_stem")
    source = existing.read_text(encoding="utf-8") if existing else spec["template"]
    text = code_box(f"{kind}_{chosen}", source)
    left, right = st.columns([1, 1])
    if left.button("Validate", key=f"{kind}_validate", width="stretch"):
        show_report(validate_source(text, safe_stem(stem)), kind)
    if right.button("Validate and save", type="primary", key=f"{kind}_save", width="stretch"):
        if save_code(project, kind, safe_stem(stem), text):
            st.rerun()
    if existing is not None:
        with st.popover("Delete file"):
            st.write(f"Delete `{kind}/{existing.name}`? Scenarios that use it will stop validating.")
            if st.button("Delete", type="primary", key=f"{kind}_delete"):
                existing.unlink()
                flash(f"Deleted {kind}/{existing.name}.", "info")
                st.rerun()


def listing(project: Path, kind: str) -> None:
    files = sorted((project / kind).glob("*.py"))
    if not files:
        st.caption(f"No {kind} yet.")
    for path in files:
        report = inspect(path)
        state = ":green[ok]" if report.get("ok") else ":red[error]"
        with st.expander(f"{path.name} · {state}"):
            show_report(report, kind)


def render() -> None:
    project = home()
    st.title("Tools & checks")
    show_flash()
    st.warning(
        "Tool and check code runs on this computer with your permissions, like any script you run. "
        "Only add code you wrote or have read.", icon=":material/shield:")
    st.markdown(
        "**Tools** are functions an agent can call; their type hints become the arguments the model "
        "fills in, and the docstring is the description it sees. **Checks** score a finished trial; "
        "they run after every trial and can be re-run later without new API calls."
    )
    tabs = st.tabs(["Tools", "Checks"])
    for tab, kind in zip(tabs, ("tools", "checks")):
        with tab:
            left, right = st.columns([3, 2])
            with left:
                st.subheader(f"Add or edit {kind}")
                editor(project, kind)
            with right:
                st.subheader(f"Your {kind}")
                listing(project, kind)
            if kind == "tools":
                st.caption("Built-in tools (no code needed): list_files, read_file, write_file, "
                           "append_file. Each scenario sets which files each agent may list, read, "
                           "write or append.")
