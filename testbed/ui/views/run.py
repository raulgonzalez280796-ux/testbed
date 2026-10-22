"""Run: start a batch (M trials, N at a time), watch it, cancel or resume it.

The UI creates the batch folder, then starts `python -m testbed resume <batch>`
as a separate process. Closing the browser does not stop a batch; progress is
read from the batch folder on disk.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from testbed.batch import create_batch, list_batches, read_summary, request_cancel, validate_code
from testbed.keys import load_keys, missing_keys
from testbed.pilot import PILOT_TRIALS, describe, plan_batch
from testbed.project import scenario_files
from testbed.scenario import load_scenario
from testbed.ui.components.common import (
    compact, flash, home, read_json, show_flash, status_text, tokens, trial_url,
)

FINAL = {"completed", "timeout", "infra_error", "budget_stopped", "cancelled", "not_run"}


def runner_alive(batch_dir: Path) -> bool:
    try:
        pid = int((batch_dir / "runner.pid").read_text())
    except (OSError, ValueError):
        return False
    try:
        finished, _ = os.waitpid(pid, os.WNOHANG)  # reaps our own finished children
        return finished == 0
    except ChildProcessError:
        pass  # started by another UI session or a previous server
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def launch(project: Path, batch_dir: Path) -> None:
    lock = project / "runs" / ".launch.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < 5:
        raise RuntimeError("Another batch was started a moment ago; wait a few seconds.")
    lock.write_text(str(batch_dir), encoding="utf-8")
    with (batch_dir / "runner.log").open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "testbed", "--home", str(project), "resume", str(batch_dir)],
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            cwd=str(project), start_new_session=True,
        )
    (batch_dir / "runner.pid").write_text(str(process.pid), encoding="utf-8")
    st.session_state.setdefault("watching", [])
    st.session_state["watching"].insert(0, str(batch_dir))


def start_section(project: Path) -> None:
    files = scenario_files(project)
    if not files:
        st.info("Create a scenario first (Scenarios page).")
        return
    names = [path.name for path in files]
    wanted = st.session_state.pop("run_scenario", None)
    index = names.index(wanted) if wanted in names else 0
    name = st.selectbox("Scenario", names, index=index, key="run_pick")
    path = project / "scenarios" / name
    try:
        scenario = load_scenario(path)
        validate_code(project, scenario)
    except Exception as error:
        st.error(f"This scenario is not valid: {str(error).splitlines()[0]}. Fix it on the Scenarios page.")
        return
    missing = missing_keys(scenario.providers, load_keys(project))
    if missing:
        st.error(f"This scenario needs {', '.join(missing)}. Add the key on the Settings page.")
        st.page_link(st.session_state["pages"]["settings"], label="Open Settings", icon=":material/key:")
        return
    left, middle, right = st.columns(3)
    trials = left.number_input("Trials in total (M)", min_value=1, max_value=100_000, value=1, key="run_trials")
    parallel = middle.number_input("At the same time (N)", min_value=1, max_value=5000,
                                   value=min(10, int(trials)), key="run_parallel")
    seed_text = right.text_input("Seed (optional)", key="run_seed",
                                 help="Same seed, same variable values and turn orders.")
    label = st.text_input("Label (optional)", key="run_label", placeholder="e.g. pilot, condition A")
    agents = ", ".join(f"{a.id}: {a.provider}/{a.model}" for a in scenario.agents)
    st.caption(f"{scenario.name} · {scenario.interaction} mode · {scenario.rounds} rounds · {agents}")

    plan = plan_batch(project, scenario, int(trials))
    confirmed = True
    if plan["needs_confirmation"]:
        if plan["needs_pilot"]:
            st.info(f"Batches above 20 trials need a token estimate first. There are no earlier "
                    f"completed trials of this scenario, so run a {PILOT_TRIALS}-trial pilot.")
            if st.button(f"Run a {PILOT_TRIALS}-trial pilot", type="primary"):
                batch_dir = create_batch(project, path, trials=PILOT_TRIALS, parallel=PILOT_TRIALS,
                                         label="pilot")
                launch(project, batch_dir)
                flash("Pilot started. When it finishes, the estimate appears here.")
                st.rerun()
            return
        st.markdown(f"**Estimate:** {describe(plan)}")
        confirmed = st.checkbox("Start this batch with that token use", key="run_confirm")
    try:
        seed = int(seed_text) if seed_text.strip() else None
    except ValueError:
        st.error("The seed must be a whole number.")
        return
    if st.button("Start", type="primary", icon=":material/play_arrow:", disabled=not confirmed):
        try:
            batch_dir = create_batch(project, path, trials=int(trials), parallel=int(parallel),
                                     seed=seed, label=label, projection=plan["projection"])
            launch(project, batch_dir)
        except Exception as error:
            st.error(str(error))
        else:
            flash(f"Started batch {batch_dir.name}.")
            st.rerun()


def progress(batch_dir: Path) -> None:
    summary = read_summary(batch_dir) or {}
    spec = read_json(batch_dir / "batch.json") or {}
    planned = summary.get("planned_trials") or spec.get("trials") or 1
    counts = summary.get("counts", {})
    done = sum(count for status, count in counts.items() if status in FINAL)
    alive = runner_alive(batch_dir)
    status = summary.get("status", "created")
    with st.container(border=True):
        title = f"**{spec.get('scenario_name', '?')}** · `{batch_dir.name}`"
        if spec.get("label"):
            title += f" · {spec['label']}"
        st.markdown(f"{title} · {status_text(status)}")
        st.progress(min(1.0, done / planned), text=f"{done} of {planned} trials finished")
        cols = st.columns(5)
        cols[0].metric("Completed", counts.get("completed", 0))
        cols[1].metric("Running", counts.get("running", 0))
        cols[2].metric("Failed", counts.get("infra_error", 0) + counts.get("timeout", 0))
        cap = (summary.get("budget") or {}).get("max_total_tokens")
        cols[3].metric("Tokens", compact(summary.get("tokens_total")),
                       help=f"Batch cap: {tokens(cap)}" if cap else "No batch cap")
        limits = summary.get("rate_limits") or {}
        in_flight = sum(item.get("in_flight", 0) for item in limits.values())
        limited = sum(item.get("rate_limited", 0) for item in limits.values())
        cols[4].metric("Requests in flight", in_flight,
                       help=f"Rate-limited responses so far: {limited}. Parallel trials at most: "
                            f"{summary.get('running_peak', 0)}.")
        if summary.get("message"):
            st.warning(summary["message"])
        running = [row["trial"] for row in summary.get("trials", []) if row.get("status") == "running"]
        if running and alive:
            st.caption("Running now. Open a trial to watch its script live, in a new tab:")
            with st.container(horizontal=True):
                for number in running[:12]:
                    st.link_button(f"Trial {number}", trial_url(batch_dir.name, number),
                                   icon=":material/open_in_new:")
            if len(running) > 12:
                st.caption(f"…and {len(running) - 12} more; Results lists them all.")
        if status == "running" and not alive:
            st.warning("The runner process is not running (it may have been stopped). Resume to finish.")
        buttons = st.columns(3)
        if status in ("running", "created") and alive:
            buttons[0].button("Cancel", key=f"cancel_{batch_dir.name}", on_click=request_cancel,
                              args=(batch_dir,), width="stretch")
        elif done < planned or status in ("running", "created", "cancelled", "stopped", "budget_stopped"):
            if buttons[0].button("Resume", key=f"resume_{batch_dir.name}", width="stretch"):
                launch(home(), batch_dir)
                st.rerun()
        if buttons[1].button("Open results", key=f"results_{batch_dir.name}", width="stretch"):
            st.session_state["results_batch"] = batch_dir.name
            st.switch_page(st.session_state["pages"]["results"])


@st.fragment(run_every="2s")
def live_batches(project: Path) -> None:
    batches = list_batches(project)
    watching = [Path(path) for path in st.session_state.get("watching", [])]
    unfinished = [b for b in batches[:20]
                  if (read_summary(b) or {}).get("status") in ("running", "created")]
    active = [b for b in unfinished if runner_alive(b)]
    # Batches whose runner stopped (e.g. the computer restarted) stay visible, with Resume.
    interrupted = [b for b in unfinished if b not in active]
    shown = list(dict.fromkeys([*active, *[b for b in watching if b.exists()], *interrupted]))[:5]
    if not shown:
        st.caption("No batch is running.")
    for batch_dir in shown:
        progress(batch_dir)


def recent(project: Path) -> None:
    rows = []
    for batch_dir in list_batches(project)[:30]:
        spec = read_json(batch_dir / "batch.json") or {}
        summary = read_summary(batch_dir) or {}
        counts = summary.get("counts", {})
        created = spec.get("created_at", "")[:19].replace("T", " ")
        rows.append({
            "batch": batch_dir.name, "created (UTC)": created,
            "scenario": spec.get("scenario_name"), "label": spec.get("label") or "",
            "status": summary.get("status", "created"),
            "completed": f"{counts.get('completed', 0)}/{spec.get('trials')}",
            "tokens": summary.get("tokens_total", 0),
        })
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.caption("No batches yet.")


def render() -> None:
    project = home()
    st.title("Run")
    show_flash()
    with st.container(border=True):
        st.subheader("Start a batch")
        start_section(project)
    st.subheader("Running and recent")
    live_batches(project)
    with st.expander("All batches"):
        recent(project)
    st.caption(f"Times are UTC. Now: {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}.")
