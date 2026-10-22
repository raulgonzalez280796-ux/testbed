"""Results: batches → trials → one trial's timeline, files, checks and raw model calls."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from testbed.batch import Summary, list_batches, load_batch, read_summary
from testbed.keys import load_keys
from testbed.logger import read_events
from testbed.trial import BatchContext, recheck_trial
from testbed.ui.components.common import flash, home, compact, read_json, show_flash, status_text, tokens


def batch_label(batch_dir: Path) -> str:
    spec = read_json(batch_dir / "batch.json") or {}
    summary = read_summary(batch_dir) or {}
    created = (spec.get("created_at") or "")[:16].replace("T", " ")
    label = f" · {spec['label']}" if spec.get("label") else ""
    return f"{created} · {spec.get('scenario_name', '?')}{label} · {summary.get('status', 'created')}"


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


# -- one trial ----------------------------------------------------------------------

def group_turns(events: list[dict]) -> list[dict]:
    turns, current = [], None
    for event in events:
        kind, data = event["event_type"], event["data"]
        if kind == "round_started":
            turns.append({"round_marker": data["round"], "order": data.get("order", [])})
        elif kind == "turn_started":
            current = {"agent": data["agent_id"], "round": data["round"], "turn": data["turn"],
                       "prompt": data["prompt"], "items": [], "end": None, "tokens": 0}
            turns.append(current)
        elif kind == "turn_skipped":
            turns.append({"agent": data["agent_id"], "round": data["round"], "turn": data["turn"],
                          "prompt": "", "items": [("note", "turn skipped: no input")], "end": None, "tokens": 0})
        elif current is not None and data.get("agent_id") == current["agent"]:
            if kind == "agent_reply":
                current["tokens"] += (data.get("usage") or {}).get("total", 0)
                current["items"].append(("reply", data))
            elif kind in ("tool_started", "tool_completed", "tool_failed", "tool_skipped", "tool_note"):
                current["items"].append((kind, data))
            elif kind in ("rate_limited", "call_retry", "model_call_failed", "empty_reply"):
                current["items"].append(("problem", {**data, "kind": kind}))
            elif kind == "turn_finished":
                current["end"] = data
        elif kind in ("run_timeout", "runtime_error", "token_cap_reached", "run_cancelled"):
            turns.append({"event": kind, "data": data})
    return turns


def short(text: str, limit: int = 4000) -> str:
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + f"\n… ({len(text) - limit:,} more characters)"


def render_turn(turn: dict) -> None:
    with st.container(border=True):
        end = turn.get("end") or {}
        st.markdown(f"**{turn['agent']}** · round {turn['round']} · turn {turn['turn']}"
                    f" · {tokens(turn['tokens'])} tokens"
                    + (" · :orange[call limit reached]" if end.get("cap_hit") else "")
                    + (" · :gray[finished]" if end.get("finished") else ""))
        if turn["prompt"]:
            with st.expander("Input", expanded=turn["turn"] == 1):
                st.text(short(turn["prompt"]))
        results = {}
        for kind, data in turn["items"]:
            if kind in ("tool_completed", "tool_failed", "tool_skipped"):
                results[data["call_id"]] = (kind, data)
        for kind, data in turn["items"]:
            if kind == "reply":
                if data.get("text"):
                    st.markdown(data["text"])
                for call in data.get("tool_calls", []):
                    args = json.dumps(call["arguments"], ensure_ascii=False)
                    outcome = results.get(call["id"])
                    st.markdown(f":material/build: `{call['name']}({short(args, 300)})`")
                    if outcome is None:
                        continue
                    outcome_kind, outcome_data = outcome
                    if outcome_kind == "tool_completed":
                        st.code(short(outcome_data.get("result", "")), language=None, wrap_lines=True)
                    elif outcome_kind == "tool_failed":
                        st.markdown(f":red[error: {outcome_data.get('error', '')}]")
                    else:
                        st.markdown(f":orange[not run: {outcome_data.get('reason', '')}]")
                if data.get("truncated"):
                    st.markdown(":orange[The reply was cut off by the output token limit.]")
            elif kind == "tool_note":
                st.caption(f"note: {data.get('message')} {json.dumps(data.get('data', {}))}")
            elif kind == "problem":
                if data["kind"] == "empty_reply":
                    st.caption("empty reply (nothing added to the history)")
                elif data["kind"] == "model_call_failed":
                    st.error(f"Model call failed: {data.get('error_kind')} "
                             f"({data.get('status_code')}): {data.get('message', '')[:300]}")
                else:
                    st.caption(f"{data['kind']}: waited {data.get('delay_seconds')} s "
                               f"(attempt {data.get('attempt')}, {data.get('error_kind')})")
            elif kind == "note":
                st.caption(data)


def timeline(events: list[dict], agents: list[str]) -> None:
    turns = group_turns(events)
    shown = st.multiselect("Agents", agents, default=agents, key="timeline_agents")
    rounds = sorted({t["round"] for t in turns if "round" in t})
    if len(rounds) > 1:
        low, high = st.slider("Rounds", min_value=rounds[0], max_value=rounds[-1],
                              value=(rounds[0], rounds[-1]), step=1)
    else:
        low = high = rounds[0] if rounds else 0
    for item in turns:
        if "round_marker" in item:
            if low <= item["round_marker"] <= high:
                st.markdown(f"##### Round {item['round_marker']} · order {' → '.join(item['order'])}")
        elif "event" in item:
            st.warning(f"{item['event']}: {json.dumps(item['data'])[:300]}")
        elif item["agent"] in shown and low <= item["round"] <= high:
            render_turn(item)


def model_calls(events: list[dict]) -> None:
    calls = [e for e in events if e["event_type"] in ("model_call", "model_call_failed")]
    if not calls:
        st.caption("No model calls.")
        return

    def label(index: int) -> str:
        data = calls[index]["data"]
        usage = ((data.get("response") or {}).get("usage") or {})
        failed = " · failed" if calls[index]["event_type"] == "model_call_failed" else ""
        return (f"{index + 1}. {data['agent_id']} · round {data['round']} · turn {data['turn']} "
                f"· call {data['call']} · {data.get('duration_seconds', 0)} s{failed}"
                + (f" · usage {json.dumps(usage)[:80]}" if usage else ""))

    index = st.selectbox("Model call", range(len(calls)), format_func=label, key="call_pick")
    data = calls[index]["data"]
    st.caption("The request exactly as passed to the provider's SDK, and the raw response.")
    left, right = st.columns(2)
    with left:
        st.markdown("**Request**")
        st.json(data.get("request") or {}, expanded=2)
    with right:
        st.markdown("**Response**" if calls[index]["event_type"] == "model_call" else "**Error**")
        st.json(data.get("response") or {key: data.get(key) for key in
                                          ("error_kind", "error_type", "status_code", "message")}, expanded=2)


def trial_view(project: Path, batch_dir: Path, index: int) -> None:
    trial_dir = batch_dir / f"trial_{index:04d}"
    result = read_json(trial_dir / "result.json")
    manifest = read_json(trial_dir / "manifest.json") or {}
    if result is None:
        st.info("This trial has not finished yet." if trial_dir.exists() else "This trial has not started.")
        return
    events = read_events(trial_dir)
    st.subheader(f"Trial {index}")
    cols = st.columns(5)
    cols[0].markdown(f"**Status**\n\n{status_text(result['status'])}")
    cols[1].markdown(f"**Stop reason**\n\n`{result.get('stop_reason')}`")
    total = (result.get("tokens") or {}).get("total")
    cols[2].metric("Tokens", compact(total), help=f"{tokens(total)} tokens")
    cols[3].metric("Duration (s)", result.get("duration_seconds"))
    cols[4].markdown("**Turns**\n\n" + ", ".join(f"{a}: {n}" for a, n in (result.get("turns") or {}).items()))
    if result.get("error"):
        st.error(f"{result['error'].get('kind')}: {result['error'].get('message', '')[:500]}")
    if manifest.get("variables"):
        st.markdown("Variables: " + ", ".join(f"`{k}` = `{v}`" for k, v in manifest["variables"].items()))
    tabs = st.tabs(["Timeline", "Final files", "Checks", "Model calls", "Manifest"])
    with tabs[0]:
        timeline(events, [agent["id"] for agent in manifest.get("agents", [])])
    with tabs[1]:
        workspace = trial_dir / "workspace"
        for path in sorted(workspace.iterdir()) if workspace.exists() else []:
            st.markdown(f"**{path.name}**")
            try:
                st.code(path.read_text(encoding="utf-8") or "(empty)", language=None, wrap_lines=True)
            except UnicodeDecodeError:
                st.caption("(binary file)")
    with tabs[2]:
        rows = [{"check": ref, "value": json.dumps(value), "error": (result.get("check_errors") or {}).get(ref, "")}
                for ref, value in (result.get("checks") or {}).items()]
        if result.get("checks_not_applicable"):
            st.info("Checks do not run on trials that failed for infrastructure reasons.")
        st.dataframe(rows, hide_index=True, width="stretch")
    with tabs[3]:
        model_calls(events)
    with tabs[4]:
        st.json(manifest, expanded=1)
    downloads = st.columns(3)
    downloads[0].download_button("result.json", json.dumps(result, indent=2), f"trial_{index:04d}_result.json",
                                 width="stretch")
    gz = trial_dir / "events.jsonl.gz"
    if gz.exists():
        downloads[1].download_button("events.jsonl.gz", gz.read_bytes(), f"trial_{index:04d}_events.jsonl.gz",
                                     width="stretch")
    downloads[2].download_button("manifest.json", json.dumps(manifest, indent=2),
                                 f"trial_{index:04d}_manifest.json", width="stretch")


def render() -> None:
    project = home()
    st.title("Results")
    show_flash()
    batches = list_batches(project)
    if not batches:
        st.info("No batches yet. Start one on the Run page.")
        return
    names = [path.name for path in batches]
    wanted = st.session_state.pop("results_batch", None)
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
    event = st.dataframe(table, hide_index=True, width="stretch", on_select="rerun",
                         selection_mode="single-row", key=f"trials_{name}")
    csv = batch_dir / "summary.csv"
    if csv.exists():
        st.download_button("Download summary.csv", csv.read_bytes(), f"{name}_summary.csv")
    selected = event.selection.rows if event and event.selection else []
    if selected:
        trial_view(project, batch_dir, int(table.iloc[selected[0]]["trial"]))
    else:
        st.caption("Select a trial in the table to open it.")
