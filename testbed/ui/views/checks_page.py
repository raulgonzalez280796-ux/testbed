"""Checks: add researcher code that scores finished trials, by pasting it or uploading a .py file.

Code is validated in a separate process before it is saved; a broken file
cannot crash the UI. It is saved as checks/<name>.py.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

from testbed.project import safe_stem
from testbed.tools.loader import inspect_file
from testbed.ui.components.code_box import code_box
from testbed.ui.components.common import flash, home, inspect, show_flash

CHECK_TEMPLATE = '''from testbed import TrialRecord, check


@check
def my_check(trial: TrialRecord) -> bool:
    """Describe what this measures. Return a bool, number or string."""
    # trial.files (final contents), trial.variables,
    # trial.tool_calls(agent=, tool=, file=, op=), trial.messages(agent=), trial.events
    return "done" in trial.files.get("notes.txt", "")
'''


def validate_source(source: str, stem: str) -> dict:
    """Inspect code in a temporary file with the same stem, so references come out right."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"{stem}.py"
        path.write_text(source, encoding="utf-8")
        return inspect_file(path)


def show_report(report: dict) -> None:
    if not report.get("ok"):
        line = f" (line {report['line']})" if report.get("line") else ""
        st.error(f"{report.get('error', 'Could not load the file')}{line}")
        return
    items = report.get("checks", [])
    if not items:
        st.warning("No @check functions found in this file.")
    for item in items:
        st.markdown(f"**`{item['ref']}`** — {item.get('description') or '(no description)'}")


def save_code(project: Path, stem: str, source: str) -> bool:
    report = validate_source(source, stem)
    if not report.get("ok") or not report.get("checks"):
        show_report(report)
        return False
    target = project / "checks" / f"{stem}.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    flash(f"Saved checks/{target.name}: " + ", ".join(item["name"] for item in report["checks"]))
    return True


def editor(project: Path) -> None:
    files = sorted((project / "checks").glob("*.py"))
    choices = ["(new file)"] + [path.name for path in files]
    chosen = st.selectbox("File", choices, key="checks_file")
    existing = None if chosen == "(new file)" else project / "checks" / chosen
    how = "Paste or write code"
    if existing is None:
        how = st.radio("Add by", ["Paste or write code", "Upload a .py file"], horizontal=True,
                       key="checks_how")
    if how == "Upload a .py file":
        upload = st.file_uploader("Python file", type=["py"], key="checks_upload")
        if upload is not None:
            stem = st.text_input("Save as", safe_stem(Path(upload.name).stem), key="checks_upload_stem")
            source = upload.getvalue().decode("utf-8", errors="replace")
            with st.expander("File contents"):
                st.code(source, language="python")
            if st.button("Validate and save", type="primary", key="checks_upload_save"):
                if save_code(project, safe_stem(stem), source):
                    st.rerun()
        return
    stem = existing.stem if existing else st.text_input(
        "File name (letters, digits and _)", "my_check", key="checks_new_stem")
    source = existing.read_text(encoding="utf-8") if existing else CHECK_TEMPLATE
    text = code_box(f"checks_{chosen}", source)
    left, right = st.columns([1, 1])
    if left.button("Validate", key="checks_validate", width="stretch"):
        show_report(validate_source(text, safe_stem(stem)))
    if right.button("Validate and save", type="primary", key="checks_save", width="stretch"):
        if save_code(project, safe_stem(stem), text):
            st.rerun()
    if existing is not None:
        with st.popover("Delete file"):
            st.write(f"Delete `checks/{existing.name}`? Scenarios that use it will stop validating.")
            if st.button("Delete", type="primary", key="checks_delete"):
                existing.unlink()
                flash(f"Deleted checks/{existing.name}.", "info")
                st.rerun()


def listing(project: Path) -> None:
    files = sorted((project / "checks").glob("*.py"))
    if not files:
        st.caption("No checks yet.")
    for path in files:
        report = inspect(path)
        state = ":green[ok]" if report.get("ok") else ":red[error]"
        with st.expander(f"{path.name} · {state}"):
            show_report(report)


def render() -> None:
    project = home()
    st.title("Checks")
    show_flash()
    st.warning(
        "Check code runs on this computer with your permissions, like any script you run. "
        "Only add code you wrote or have read.", icon=":material/shield:")
    st.markdown(
        "**Checks** score a finished trial: for example, did output.txt end up with the token? "
        "They run after every trial, their results become columns in Results, and they can be "
        "re-run later on saved trials without new API calls. A scenario lists the checks it uses "
        "on its *Checks & budget* tab."
    )
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Add or edit checks")
        editor(project)
    with right:
        st.subheader("Your checks")
        listing(project)
