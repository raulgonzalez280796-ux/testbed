"""Results: batches → check summaries → trials. Each trial opens on its own page (trial_page.py)."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from testbed.batch import Summary, list_batches, load_batch, read_summary
from testbed.keys import load_keys
from testbed.trial import BatchContext, recheck_trial
from testbed.ui.components.common import (
    batch_label, compact, flash, home, page_link, read_json, show_flash, status_text, tokens, trial_url,
)


def check_overview(rows: list[dict], refs: list[str]) -> None:
    completed = [row for row in rows if row.get("status") == "completed"]
    if not refs:
        st.caption("This scenario has no checks.")
        return
    lines = []
    for ref in refs:
        values = [row.get(f"check:{ref}") for row in completed if row.get(f"check:{ref}") is not None]
        if not values:
            lines.append({"check": ref, "summary": "no values", "n": 0})
        elif all(isinstance(value, bool) for value in values):
            true = sum(values)
            lines.append({"check": ref, "summary": f"true in {true} of {len(values)} ({true / len(values):.0%})",
                          "n": len(values)})
        elif all(isinstance(value, (int, float)) for value in values):
            series = pd.Series(values, dtype="float")
            lines.append({"check": ref, "summary": f"mean {series.mean():.3g}, min {series.min():.3g}, "
                                                   f"max {series.max():.3g}", "n": len(values)})
        else:
            lines.append({"check": ref, "summary": f"{len(set(map(str, values)))} distinct values",
                          "n": len(values)})
    st.dataframe(lines, hide_index=True, width="stretch")
    st.caption("Over completed trials only; failed trials (infra_error, timeout…) are never counted.")


def recheck_batch(project: Path, batch_dir: Path, from_project: bool) -> None:
    spec, scenario = load_batch(batch_dir)
    if from_project:
        frozen = batch_dir / "code" / "checks"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        if frozen.exists():
            shutil.copytree(frozen, batch_dir / "code" / f"checks.before-{stamp}")
        frozen.mkdir(parents=True, exist_ok=True)
        for stem in {ref.split(":", 1)[0] for ref in scenario.checks}:
            source = project / "checks" / f"{stem}.py"
            if source.exists():
                shutil.copy2(source, frozen / source.name)
    batch = BatchContext(batch_dir, spec["batch_id"], scenario, spec["seed"], load_keys(project))
    summary = Summary(batch_dir, spec, scenario)
    for index in summary.rows:
        if (batch.trial_dir(index) / "result.json").exists():
            recheck_trial(batch, batch.trial_dir(index))
            summary.refresh(index)
    previous = read_summary(batch_dir) or {}
    for field in ("status", "started_at", "finished_at", "message", "running_peak", "rate_limits"):
        if field in previous:
            setattr(summary, field, previous[field])
    summary.save()


def render() -> None:
    project = home()
    st.title("Results")
    show_flash()
    batches = list_batches(project)
    if not batches:
        st.info("No batches yet. Start one on the Run page.")
        return
    names = [path.name for path in batches]
    wanted = st.session_state.pop("results_batch", None) or st.query_params.get("batch")
    if wanted in names:
        st.session_state["results_pick"] = wanted
    name = st.selectbox("Batch", names, key="results_pick",
                        format_func=lambda n: batch_label(project / "runs" / n))
    batch_dir = project / "runs" / name
    spec = read_json(batch_dir / "batch.json") or {}
    summary = read_summary(batch_dir) or {}
    rows = summary.get("trials", [])
    counts = summary.get("counts", {})
    cols = st.columns(5)
    cols[0].markdown(f"**Status**\n\n{status_text(summary.get('status'))}")
    cols[1].metric("Completed", f"{counts.get('completed', 0)} / {spec.get('trials', '?')}")
    cols[2].metric("Failed", counts.get("infra_error", 0) + counts.get("timeout", 0))
    cols[3].metric("Tokens", compact(summary.get("tokens_total")),
                   help=f"{tokens(summary.get('tokens_total'))} tokens")
    cols[4].metric("Parallel at most", summary.get("running_peak", 0))
    if summary.get("message"):
        st.warning(summary["message"])

    _, scenario = load_batch(batch_dir)
    st.markdown("##### Checks")
    check_overview(rows, scenario.checks)
    with st.popover("Re-run checks"):
        st.write("Re-running checks makes no API calls.")
        if st.button("With the batch's saved copy of the checks"):
            recheck_batch(project, batch_dir, from_project=False)
            flash("Checks re-run.")
            st.rerun()
        if st.button("With the current checks from your project",
                     help="Copies checks/*.py into this batch (the old copy is kept) and re-runs them."):
            recheck_batch(project, batch_dir, from_project=True)
            flash("Checks updated from the project and re-run.")
            st.rerun()

    st.markdown("##### Trials")
    statuses = sorted({row.get("status") for row in rows if row.get("status")})
    keep = st.multiselect("Status", statuses, default=statuses, key=f"status_filter_{name}")
    table = pd.DataFrame([row for row in rows if row.get("status") in keep])
    if table.empty:
        st.caption("No trials match.")
        return
    table = table.drop(columns=[column for column in ("dir",) if column in table])
    table.insert(0, "open", [trial_url(name, int(number)) for number in table["trial"]])
    st.caption("**Open ↗** shows a trial's script on its own page, in a new browser tab; running trials too.")
    event = st.dataframe(table, hide_index=True, width="stretch", on_select="rerun",
                         selection_mode="single-row", key=f"trials_{name}",
                         column_config={"open": st.column_config.LinkColumn("", display_text="Open ↗", width=80)})
    csv = batch_dir / "summary.csv"
    if csv.exists():
        st.download_button("Download summary.csv", csv.read_bytes(), f"{name}_summary.csv")
    selected = event.selection.rows if event and event.selection else []
    if selected:
        number = int(table.iloc[selected[0]]["trial"])
        left, right, _ = st.columns([1, 1, 3])
        with left:
            page_link("trial", f"Open trial {number}", {"batch": name, "trial": number},
                      icon=":material/subject:")
        right.link_button("In a new tab", trial_url(name, number), icon=":material/open_in_new:")
    else:
        st.caption("Or select a trial in the table.")
