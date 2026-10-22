"""Trial: one trial of a batch on its own page, as a chronological script.

Opened as /trial?batch=<batch id>&trial=<n> from Results or Run, often in a new
browser tab, so several trials can be open side by side. While the trial runs,
the script re-reads its log every 2 seconds; the order stays oldest to newest.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from testbed.batch import list_batches, read_summary
from testbed.logger import read_events
from testbed.script import (
    GROUPS, Section, Step, activity, build_script, clock, filter_steps, script_html, script_markdown,
    trial_info,
)
from testbed.ui.components.common import (
    batch_label, compact, home, page_link, read_json, show_flash, status_text, tokens,
)

BADGES = ["blue", "green", "violet", "orange", "red", "yellow", "gray"]
PAGE_SIZE = 300      # steps drawn at once; more behind "Show more"
LONG = 4000          # characters shown before "Show all"
STATUS_ICONS = {"ok": ":green[✓]", "error": ":red[✗]", "warning": ":orange[!]", "running": ":blue[…]"}


def md(text: str) -> str:
    """Text shown inside st.markdown exactly as written (no Markdown or colors)."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|<>~$:])", r"\\\1", str(text))


def chosen_trial(project: Path) -> tuple[Path, int] | None:
    batch, trial = st.query_params.get("batch"), st.query_params.get("trial")
    if not batch or not trial or not str(trial).isdigit():
        return None
    if Path(batch).name != batch or batch.startswith("."):  # a batch id, never a path
        return None
    batch_dir = project / "runs" / batch
    return (batch_dir, int(trial)) if batch_dir.is_dir() else None


def picker(project: Path) -> None:
    st.title("Trial")
    st.info("Open a trial from Results or Run, or pick one here.")
    batches = list_batches(project)
    if not batches:
        st.caption("No batches yet.")
        return
    name = st.selectbox("Batch", [path.name for path in batches],
                        format_func=lambda n: batch_label(project / "runs" / n), key="trial_pick_batch")
    rows = (read_summary(project / "runs" / name) or {}).get("trials", [])
    numbers = [row["trial"] for row in rows] or [1]
    index = st.selectbox("Trial", numbers, key="trial_pick_index")
    if st.button("Open", type="primary"):
        st.query_params.from_dict({"batch": name, "trial": str(index)})
        st.rerun()


# -- drawing steps --------------------------------------------------------------------

def draw_body(section: Section, key: str) -> None:
    text = section.body if section.body.strip() else "(empty)"
    if section.style == "warning":
        st.caption(f":orange[{md(text)}]")
        return
    language = "json" if section.style == "code" else None
    st.code(text[:LONG], language=language, wrap_lines=True)
    if len(text) > LONG:
        with st.expander(f"Show all ({len(text):,} characters)", key=f"{key}_all"):
            st.code(text, language=language, wrap_lines=True)


def draw_step(step: Step, colors: dict, raw: bool) -> None:
    stamp = clock(step.elapsed)
    if step.kind == "round":
        st.markdown(f"##### {md(step.title)} · :gray[{md(step.meta)} · {stamp}]")
        return
    with st.container(border=True):
        title = step.title
        badge = ""
        if step.agent and title.startswith(step.agent):
            title = title[len(step.agent):].lstrip()
            badge = f":{colors.get(step.agent, 'gray')}-badge[{md(step.agent)}] "
        line = f"`{stamp}` {badge}**{md(title)}** {STATUS_ICONS.get(step.status, '')}"
        if step.meta:
            line += f"  \n:gray[{md(step.meta)}]"
        st.markdown(line)
        for number, section in enumerate(step.sections):
            key = f"step_{step.seq}_{number}"
            if section.collapsed:
                with st.expander(section.label, key=key):
                    draw_body(section, key)
            else:
                if section.label:
                    color = "red" if section.style == "error" else "gray"
                    st.markdown(f":{color}[{md(section.label)}]")
                draw_body(section, key)
        if raw:
            with st.expander("Raw events", key=f"step_{step.seq}_raw"):
                st.json(step.raw, expanded=False)


def draw_script(events: list[dict], manifest: dict, filters: dict, raw: bool) -> None:
    steps = build_script(events, manifest)
    shown = filter_steps(steps, **filters)
    agents = [agent["id"] for agent in manifest.get("agents", [])]
    colors = {agent: BADGES[i % len(BADGES)] for i, agent in enumerate(agents)}
    text = filters.get("text", "").strip()
    st.caption(f"{len(shown)} of {len(steps)} steps" + (f" contain “{text}”" if text else ""))
    limit = st.session_state.get("script_limit", PAGE_SIZE)
    for step in shown[:limit]:
        draw_step(step, colors, raw)
    if len(shown) > limit:
        st.button(f"Show more ({len(shown) - limit:,} more steps)", key="script_more",
                  on_click=lambda: st.session_state.update(script_limit=limit + PAGE_SIZE))


@st.fragment(run_every="2s")
def live_script(trial_dir: Path, manifest: dict, filters: dict, raw: bool, follow: bool) -> None:
    if (trial_dir / "result.json").exists():
        st.rerun()  # finished: redraw the whole page with the final header
    events = read_events(trial_dir)
    now = datetime.now(timezone.utc)
    started = next((e for e in events if e["event_type"] == "run_started"), None)
    seconds = (now - datetime.fromisoformat(started["timestamp"])).total_seconds() if started else None
    rounds = [e["data"]["round"] for e in events if e["event_type"] == "round_started"]
    st.info(" · ".join(part for part in (
        f"**Running** {clock(seconds)}", f"round {rounds[-1]}" if rounds else "", activity(events, now),
    ) if part) + "  \nNew steps appear below as they happen.", icon=":material/autorenew:")
    if follow:  # a scrolling box that keeps the newest step in view
        with st.container(height=650, autoscroll=True, border=False):
            draw_script(events, manifest, filters, raw)
    else:
        draw_script(events, manifest, filters, raw)


def filter_row(manifest: dict, running: bool) -> tuple[dict, bool, bool]:
    agents = [agent["id"] for agent in manifest.get("agents", [])]
    tools = sorted({tool["name"] for agent in manifest.get("agents", []) for tool in agent.get("tools", [])})
    rounds = int(manifest.get("rounds") or 1)
    columns = st.columns([2, 3, 3, 2])
    chosen_agents = columns[0].multiselect("Agents", agents, default=agents, key="script_agents")
    groups = columns[1].multiselect("Show", list(GROUPS), default=list(GROUPS), key="script_groups")
    chosen_tools = columns[2].multiselect("Tools", tools, default=tools, key="script_tools",
                                          help="Which tools' calls to show")
    chosen_rounds = (columns[3].slider("Rounds", 1, rounds, (1, rounds), key="script_rounds")
                     if rounds > 1 else None)
    left, right = st.columns([3, 1], vertical_alignment="bottom")
    text = left.text_input("Search", key="script_search",
                           placeholder="Text anywhere in a step, for example a token or a file name")
    with right:
        raw = st.toggle("Raw events", key="script_raw", help="Show the logged events under each step.")
        follow = running and st.toggle("Follow the newest steps", value=True, key="script_follow",
                                       help="While the trial runs, keep the latest steps in view.")
    filters = {
        # None means "no filter", so steps outside the scenario's lists (an unknown tool) still show.
        "agents": None if set(chosen_agents) == set(agents) else chosen_agents,
        "groups": None if set(groups) == set(GROUPS) else groups,
        "tools": None if set(chosen_tools) == set(tools) else chosen_tools,
        "rounds": chosen_rounds,
        "text": text,
    }
    return filters, raw, follow


# -- the rest of the page -----------------------------------------------------------

def model_calls(events: list[dict]) -> None:
    calls = [e for e in events if e["event_type"] in ("model_call", "model_call_failed")]
    if not calls:
        st.caption("No model calls.")
        return

    def label(index: int) -> str:
        data = calls[index]["data"]
        failed = " · failed" if calls[index]["event_type"] == "model_call_failed" else ""
        return (f"{index + 1}. {data['agent_id']} · round {data['round']} · turn {data['turn']} "
                f"· call {data['call']} · {data.get('duration_seconds', 0)} s{failed}")

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


def raw_log(events: list[dict]) -> None:
    st.markdown("**Model calls**")
    model_calls(events)
    st.markdown("**Every event**")
    if events:
        index = st.selectbox(
            "Event", range(len(events)), key="event_pick",
            format_func=lambda i: f"{events[i]['seq']}. {events[i]['event_type']}"
                                  + (f" · {events[i]['data'].get('agent_id')}" if events[i]["data"].get("agent_id") else ""))
        st.json(events[index], expanded=2)


def final_files(trial_dir: Path, finished: bool) -> None:
    workspace = trial_dir / "workspace"
    if not finished:
        st.caption("The trial is still running: these are the files as they are now.")
    for path in sorted(workspace.iterdir()) if workspace.exists() else []:
        st.markdown(f"**{md(path.name)}**")
        try:
            st.code(path.read_text(encoding="utf-8") or "(empty)", language=None, wrap_lines=True)
        except UnicodeDecodeError:
            st.caption("(binary file)")


def checks(result: dict | None) -> None:
    if result is None:
        st.caption("Checks run when the trial ends.")
        return
    if result.get("checks_not_applicable"):
        st.info("Checks do not run on trials that failed for infrastructure reasons.")
    rows = [{"check": ref, "value": json.dumps(value), "error": (result.get("check_errors") or {}).get(ref, "")}
            for ref, value in (result.get("checks") or {}).items()]
    st.dataframe(rows, hide_index=True, width="stretch")


def navigation(batch_dir: Path, index: int, numbers: list[int]) -> None:
    with st.container(horizontal=True):
        if index - 1 in numbers:
            page_link("trial", f"← Trial {index - 1}", {"batch": batch_dir.name, "trial": index - 1})
        if index + 1 in numbers:
            page_link("trial", f"Trial {index + 1} →", {"batch": batch_dir.name, "trial": index + 1})
        page_link("results", "All trials", {"batch": batch_dir.name})


def stopped_early(manifest: dict, events: list[dict]) -> bool:
    """A trial with no result long after its timeout: its runner stopped (resume the batch)."""
    started = next((e for e in events if e["event_type"] == "run_started"), None)
    if started is None:
        return False
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(started["timestamp"])).total_seconds()
    return age > float(manifest.get("timeout_seconds") or 720) + 120


@st.fragment(run_every="2s")
def wait_for_start(trial_dir: Path) -> None:
    if (trial_dir / "manifest.json").exists():
        st.rerun()
    st.info("This trial has not started yet. The page opens it as soon as it starts.",
            icon=":material/hourglass_empty:")


def render() -> None:
    project = home()
    show_flash()
    chosen = chosen_trial(project)
    if chosen is None:
        picker(project)
        return
    batch_dir, index = chosen
    trial_dir = batch_dir / f"trial_{index:04d}"
    spec = read_json(batch_dir / "batch.json") or {}
    rows = (read_summary(batch_dir) or {}).get("trials", [])
    numbers = [row["trial"] for row in rows] or list(range(1, int(spec.get("trials") or 1) + 1))

    st.title(f"Trial {index} of {len(numbers)}")
    st.caption(f"{spec.get('scenario_name', '?')} · batch `{batch_dir.name}`"
               + (f" · {spec['label']}" if spec.get("label") else ""))
    navigation(batch_dir, index, numbers)
    manifest = read_json(trial_dir / "manifest.json")
    if manifest is None:
        wait_for_start(trial_dir)
        return
    result = read_json(trial_dir / "result.json")
    events = read_events(trial_dir)
    running = result is None and not stopped_early(manifest, events)

    status = result["status"] if result else ("running" if running else "stopped")
    cols = st.columns(5)
    cols[0].markdown(f"**Status**\n\n{status_text(status)}")
    cols[1].markdown(f"**Stop reason**\n\n`{(result or {}).get('stop_reason') or '–'}`")
    total = ((result or {}).get("tokens") or {}).get("total")
    cols[2].metric("Tokens", compact(total), help=f"{tokens(total)} tokens")
    cols[3].metric("Duration (s)", (result or {}).get("duration_seconds") or "–")
    cols[4].markdown("**Turns**\n\n" + (", ".join(f"{a}: {n}" for a, n in ((result or {}).get("turns") or {}).items())
                                        or "–"))
    if result and result.get("error"):
        st.error(f"{result['error'].get('kind')}: {result['error'].get('message', '')[:500]}")
    if result is None and not running:
        st.warning("This trial never finished: the batch stopped while it ran (for example the computer "
                   "restarted). Resume the batch on the Run page to run it again.")

    tabs = st.tabs(["Script", "Final files", "Checks", "Raw log", "Manifest"])
    with tabs[0]:
        filters, raw, follow = filter_row(manifest, running)
        if running:
            live_script(trial_dir, manifest, filters, raw, follow)
        else:
            draw_script(events, manifest, filters, raw)
    with tabs[1]:
        final_files(trial_dir, finished=result is not None)
    with tabs[2]:
        checks(result)
    with tabs[3]:
        raw_log(events)
    with tabs[4]:
        st.json(manifest, expanded=1)

    steps = build_script(events, manifest)
    info = trial_info(trial_dir)
    stem = f"{batch_dir.name}_trial_{index:04d}"
    so_far = "" if result else " (so far)"
    downloads = st.columns(5)
    downloads[0].download_button(f"Script .md{so_far}", script_markdown(steps, info), f"{stem}_script.md",
                                 width="stretch", help="The whole trial, whatever the filters")
    downloads[1].download_button(f"Script .html{so_far}", script_html(steps, info), f"{stem}_script.html",
                                 width="stretch", help="Opens in any browser")
    if result:
        downloads[2].download_button("result.json", json.dumps(result, indent=2), f"{stem}_result.json",
                                     width="stretch")
    gz = trial_dir / "events.jsonl.gz"
    if gz.exists():
        downloads[3].download_button("events.jsonl.gz", gz.read_bytes(), f"{stem}_events.jsonl.gz", width="stretch")
    downloads[4].download_button("manifest.json", json.dumps(manifest, indent=2), f"{stem}_manifest.json",
                                 width="stretch")
