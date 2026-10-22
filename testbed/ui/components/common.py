"""Small helpers shared by the UI pages. No experiment logic lives in the UI."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode

import streamlit as st

from testbed import providers
from testbed.api import resolve_home
from testbed.batch import read_summary
from testbed.keys import load_keys
from testbed.tools.library import load_notes
from testbed.tools.loader import inspect_file

STATUS_COLORS = {
    "completed": "green", "finished": "green", "running": "blue", "queued": "gray",
    "timeout": "orange", "infra_error": "red", "budget_stopped": "orange",
    "cancelled": "gray", "not_run": "gray", "incomplete": "orange", "stopped": "red",
    "created": "gray",
}


def home() -> Path:
    return resolve_home()


def status_text(status: str | None) -> str:
    status = status or "unknown"
    return f":{STATUS_COLORS.get(status, 'gray')}[{status}]"


def flash(message: str, kind: str = "success") -> None:
    st.session_state.setdefault("_flash", []).append((kind, message))


def show_flash() -> None:
    for kind, message in st.session_state.pop("_flash", []):
        getattr(st, kind)(message)


def keys() -> dict[str, str]:
    return load_keys(home())


def provider_label(name: str, available: dict[str, str] | None = None) -> str:
    available = keys() if available is None else available
    try:
        label = providers.provider_info(name).label
    except KeyError:
        return f"{name} (unknown)"
    return label if name in available else f"{label} (no key)"


@st.cache_data(show_spinner=False)
def _inspect_cached(path: str, mtime: float, size: int, kind: str | None) -> dict:
    return inspect_file(Path(path), kind=kind)


def inspect(path: Path, kind: str | None = None) -> dict:
    """inspect_file, cached until the file changes. kind="tools" adds the tool-file rules."""
    stat = path.stat()
    return _inspect_cached(str(path), stat.st_mtime, stat.st_size, kind)


def custom_tools(project: Path) -> list[dict]:
    found = []
    for path in sorted((project / "tools").glob("*.py")):
        report = inspect(path, "tools")
        if report.get("ok"):
            found.extend(report["tools"])
    return found


def custom_checks(project: Path) -> list[dict]:
    found = []
    for path in sorted((project / "checks").glob("*.py")):
        report = inspect(path)
        if report.get("ok"):
            found.extend(report["checks"])
    return found


def first_line(text: str) -> str:
    return (text or "").strip().splitlines()[0] if (text or "").strip() else ""


def tool_options(project: Path) -> dict[str, str]:
    """Tool reference -> label for pickers: every tool in tools/, with your note
    (or else the description the model sees)."""
    notes = load_notes(project / "tools")
    options = {}
    for item in custom_tools(project):
        about = notes.get(item["ref"]) or first_line(item.get("description", ""))
        options[item["ref"]] = f"{item['ref']} — {about}" if about else item["ref"]
    return options


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def tokens(value) -> str:
    return "–" if value in (None, "") else f"{int(value):,}"


def compact(value) -> str:
    """Short number for metric tiles: 79,916 -> 79.9K, 1,250,000 -> 1.25M."""
    if value in (None, ""):
        return "–"
    value = int(value)
    for size, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(value) >= size:
            return f"{value / size:.3g}{suffix}"
    return str(value)


def batch_label(batch_dir: Path) -> str:
    spec = read_json(batch_dir / "batch.json") or {}
    summary = read_summary(batch_dir) or {}
    created = (spec.get("created_at") or "")[:16].replace("T", " ")
    label = f" · {spec['label']}" if spec.get("label") else ""
    return f"{created} · {spec.get('scenario_name', '?')}{label} · {summary.get('status', 'created')}"


def trial_url(batch: str, index: int) -> str:
    """The trial page's address, relative to the app: trial?batch=...&trial=3."""
    return "trial?" + urlencode({"batch": batch, "trial": index})


def page_link(name: str, label: str, query_params: dict | None = None, **options) -> None:
    """st.page_link to one of the app's pages; a plain link where the app's pages aren't registered (tests)."""
    page = (st.session_state.get("pages") or {}).get(name)
    if page is not None:
        st.page_link(page, label=label, query_params=query_params, **options)
    else:
        st.link_button(label, f"{name}?{urlencode(query_params or {})}")
