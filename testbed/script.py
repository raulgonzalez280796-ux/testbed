"""The trial script: one trial's events as readable steps, in the order they happened.

One logger writes a trial's events and agents take turns one after another, so
the log's `seq` is the true order. Each step says what is new at that point:
what an agent received, what it wrote, which tool it called and what came back.
The UI's trial page, the Markdown/HTML downloads and `python -m testbed script`
all use these steps. Nothing here imports Streamlit.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .logger import read_events

# Kinds of step, and the groups the trial page filters by.
GROUPS = {
    "Inputs": ("input",),
    "Replies": ("reply",),
    "Tool calls": ("tool",),
    "Notes and problems": ("note", "problem"),
    "Rounds and turn ends": ("start", "round", "turn_end", "end"),
}
FINAL_EVENTS = ("run_finished",)
PROBLEM_EVENTS = ("rate_limited", "call_retry", "model_call_failed", "run_timeout", "run_cancelled",
                  "runtime_error", "token_cap_reached")


@dataclass
class Section:
    label: str
    body: str
    style: str = "text"        # text (verbatim), code (JSON), error, warning
    collapsed: bool = False


@dataclass
class Step:
    seq: int
    kind: str                  # start, round, input, reply, tool, note, problem, turn_end, end
    title: str
    elapsed: float | None = None
    agent: str | None = None
    round: int | None = None
    turn: int | None = None
    meta: str = ""             # one line of details: call, tokens, duration, file and action
    status: str | None = None  # ok, error, warning, running
    sections: list[Section] = field(default_factory=list)
    tool: str | None = None
    raw: list[dict] = field(default_factory=list)

    def search_text(self) -> str:
        return "\n".join([self.title, self.meta, *(s.label + "\n" + s.body for s in self.sections)]).lower()


def _time(event: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(event["timestamp"])
    except (KeyError, TypeError, ValueError):
        return None


def clock(seconds: float | None) -> str:
    """+MM:SS.s since the trial started (+H:MM:SS after an hour)."""
    if seconds is None:
        return ""
    minutes, rest = divmod(max(seconds, 0.0), 60)
    if minutes >= 60:
        hours, minutes = divmod(int(minutes), 60)
        return f"+{hours}:{minutes:02d}:{int(rest):02d}"
    return f"+{int(minutes):02d}:{rest:04.1f}"


def _json(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1)


def call_text(name: str, arguments: dict, limit: int = 120) -> tuple[str, bool]:
    """name(arg="value", ...) for a title, and whether it had to be shortened."""
    parts = ", ".join(f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in (arguments or {}).items())
    text = f"{name}({parts})"
    return (text, False) if len(text) <= limit else (text[:limit - 2] + "…)", True)


def _usage_text(usage: dict | None) -> str:
    usage = usage or {}
    total = usage.get("total") or (usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
    return f"{total:,} tokens" if total else ""


def _join(*parts) -> str:
    return " · ".join(part for part in parts if part)


def build_script(events: list[dict], manifest: dict | None = None) -> list[Step]:
    """Readable steps from a trial's events (finished or still running)."""
    manifest = manifest or {}
    agents = {agent["id"]: agent for agent in manifest.get("agents", [])}
    events = sorted(events, key=lambda event: event.get("seq", 0))
    start = next((_time(e) for e in events if e["event_type"] == "run_started"), None)
    start = start or (_time(events[0]) if events else None)

    def elapsed(event: dict) -> float | None:
        moment = _time(event)
        return (moment - start).total_seconds() if moment and start else None

    steps: list[Step] = []
    tools: dict[str, Step] = {}          # call_id -> its step, until the result arrives
    model_calls: dict[tuple, dict] = {}  # (agent, round, turn, call) -> model_call event
    empty: set[tuple] = set()
    seen_agents: set[str] = set()

    for event in events:
        kind, data, seq = event["event_type"], event.get("data") or {}, event.get("seq", 0)
        agent = data.get("agent_id")
        where = dict(agent=agent, round=data.get("round"), turn=data.get("turn"))
        at = elapsed(event)

        if kind == "run_started":
            lines = [f"{spec['id']}: {spec.get('provider')} / {spec.get('model')}" for spec in agents.values()]
            sections = [Section("Agents", "\n".join(lines))] if lines else []
            if manifest.get("variables"):
                sections.append(Section("Variables", "\n".join(
                    f"{name} = {value}" for name, value in manifest["variables"].items())))
            steps.append(Step(seq, "start", "Trial started", at, sections=sections, raw=[event]))

        elif kind == "round_started":
            steps.append(Step(seq, "round", f"Round {data['round']}", at, round=data["round"],
                              meta="order " + " → ".join(data.get("order", [])), raw=[event]))

        elif kind == "turn_started":
            sections = []
            if agent not in seen_agents and agent in agents:
                spec = agents[agent]
                if spec.get("system_prompt"):
                    sections.append(Section("System prompt (sent with every call)", spec["system_prompt"],
                                            collapsed=True))
                listed = "\n".join(f"{tool['name']}: {(tool.get('description') or '').strip()}"
                                   for tool in spec.get("tools", []))
                sections.append(Section("Tools it can use", listed or "(none)", collapsed=True))
            seen_agents.add(agent)
            messages = data.get("messages")
            if messages:
                sections.append(Section("Messages from the other agents", "\n\n".join(
                    f"[{m.get('sender')}] (round {m.get('round')}): {m.get('text', '')}" for m in messages)))
            if "instruction" in data:
                if data["instruction"].strip():
                    sections.append(Section("Instruction" if messages else "Input", data["instruction"]))
            else:  # trials from before the instruction was logged separately
                sections.append(Section("Input", data.get("prompt", "")))
            steps.append(Step(seq, "input", f"{agent} received", at, **where,
                              meta=f"round {data.get('round')} · turn {data.get('turn')}",
                              sections=sections, raw=[event]))

        elif kind == "turn_skipped":
            steps.append(Step(seq, "turn_end", f"{agent} had no input, so its turn was skipped", at, **where,
                              meta=data.get("reason", ""), raw=[event]))

        elif kind == "model_call":
            model_calls[(agent, data.get("round"), data.get("turn"), data.get("call"))] = event

        elif kind == "empty_reply":
            empty.add((agent, data.get("round"), data.get("turn"), data.get("call")))

        elif kind == "agent_reply":
            key = (agent, data.get("round"), data.get("turn"), data.get("call"))
            call_event = model_calls.pop(key, None)
            raw = [call_event, event] if call_event else [event]
            duration = (call_event or {}).get("data", {}).get("duration_seconds")
            calls = data.get("tool_calls") or []
            text = data.get("text") or ""
            if key in empty:
                title, status = f"{agent} gave an empty reply", "warning"
            elif text:
                title, status = f"{agent} wrote", None
            else:
                title, status = f"{agent} called {len(calls)} tool{'s' * (len(calls) != 1)}", None
            sections = [Section("", text)] if text else []
            if key in empty:
                sections.append(Section("", "Nothing was added to the agent's history.", "warning"))
            if data.get("truncated"):
                sections.append(Section("", "The reply was cut off by the output token limit; its tool "
                                            "calls were not run.", "warning"))
                status = "warning"
            meta = _join(f"call {data.get('call')}", _usage_text(data.get("usage")),
                         f"{duration:.1f} s" if duration is not None else "",
                         f"stop: {data.get('stop_reason')}" if data.get("stop_reason") else "")
            steps.append(Step(seq, "reply", title, at, **where, meta=meta, status=status,
                              sections=sections, raw=raw))

        elif kind == "tool_started":
            title, shortened = call_text(data.get("tool", "?"), data.get("arguments") or {})
            step = Step(seq, "tool", f"{agent} called {title}", at, **where, status="running",
                        tool=data.get("tool"), meta=_join(
                            data.get("file") and f"{data['file']} · {data.get('op') or 'any action'}",
                            not data.get("file") and data.get("op") or ""), raw=[event])
            if shortened:
                step.sections.append(Section("Arguments", _json(data.get("arguments")), "code"))
            tools[data.get("call_id")] = step
            steps.append(step)

        elif kind in ("tool_completed", "tool_failed"):
            step = tools.pop(data.get("call_id"), None)
            if step is None:  # a result without a start: should not happen, but never drop it
                step = Step(seq, "tool", f"{agent} called {data.get('tool', '?')}", at, **where,
                            tool=data.get("tool"))
                steps.append(step)
            step.raw.append(event)
            took = (at - step.elapsed) if at is not None and step.elapsed is not None else None
            if took is not None:
                step.meta = _join(step.meta, f"{took:.2f} s")
            if kind == "tool_completed":
                step.status = "ok"
                step.sections.append(Section("Result", _json(data.get("result", ""))))
            else:
                step.status = "error"
                step.sections.append(Section(f"Error ({data.get('error_type')})", data.get("error", ""), "error"))

        elif kind == "tool_skipped":
            title, _ = call_text(data.get("tool", "?"), data.get("arguments") or {})
            steps.append(Step(seq, "tool", f"{agent} called {title}", at, **where, status="warning",
                              tool=data.get("tool"), raw=[event], sections=[
                                  Section("Not run", f"reason: {data.get('reason')}", "warning")]))

        elif kind == "tool_note":
            steps.append(Step(seq, "note", f"Note from {data.get('tool')}: {data.get('message')}", at, **where,
                              tool=data.get("tool"), raw=[event], sections=[
                                  Section("", _json(data.get("data") or {}), "code")] if data.get("data") else []))

        elif kind == "turn_finished":
            if data.get("finished"):
                why = "the agent is finished (no more turns)"
            elif data.get("cap_hit"):
                why = "call limit reached: the agent sees the last tool results at the start of its next turn"
            else:
                why = "answered without a tool call"
            steps.append(Step(seq, "turn_end", f"{agent} ended its turn", at, **where,
                              meta=_join(why, f"{data.get('model_calls')} model call(s)"), raw=[event]))

        elif kind in PROBLEM_EVENTS:
            steps.append(_problem(seq, kind, data, at, where, event))

        elif kind == "run_finished":
            steps.append(Step(seq, "end", "Trial ended", at, raw=[event],
                              status="ok" if data.get("status") == "completed" else "error",
                              meta=_join(f"status: {data.get('status')}", f"stop reason: {data.get('stop_reason')}",
                                         f"{data.get('tokens', 0):,} tokens" if data.get("tokens") else "")))
        else:  # an event this version doesn't know: still shown, never dropped
            steps.append(Step(seq, "note", kind, at, **where, raw=[event],
                              sections=[Section("", _json(data), "code")]))
    return steps


def _problem(seq, kind, data, at, where, event) -> Step:
    agent = where["agent"]
    if kind in ("rate_limited", "call_retry"):
        title = f"{agent} retries its model call" + (" (rate limited)" if kind == "rate_limited" else "")
        meta = _join(f"attempt {data.get('attempt')}", f"waits {data.get('delay_seconds')} s",
                     data.get("error_kind"), str(data.get("status_code") or ""))
        return Step(seq, "problem", title, at, **where, meta=meta, status="warning", raw=[event],
                    sections=[Section("", data.get("message", ""), "warning")] if data.get("message") else [])
    if kind == "model_call_failed":
        return Step(seq, "problem", f"{agent} got no reply: the model call failed", at, **where, status="error", raw=[event],
                    meta=_join(data.get("error_kind"), str(data.get("status_code") or ""),
                               f"after {data.get('attempts')} attempt(s)"),
                    sections=[Section("", data.get("message", ""), "error")])
    titles = {"run_timeout": "The trial timed out", "run_cancelled": "The trial was cancelled",
              "runtime_error": "The testbed hit an unexpected error",
              "token_cap_reached": "The trial reached its token cap"}
    return Step(seq, "problem", titles[kind], at, status="error", raw=[event],
                sections=[Section("", _json(data), "code")] if data else [])


# -- while a trial runs ------------------------------------------------------------

def activity(events: list[dict], now: datetime) -> str:
    """What a running trial is doing, from its last events."""
    if not events:
        return "starting"
    last = max(events, key=lambda event: event.get("seq", 0))
    moment = _time(last)
    waited = f" ({(now - moment).total_seconds():.0f} s)" if moment else ""
    finished = {e["data"].get("call_id") for e in events if e["event_type"] in ("tool_completed", "tool_failed")}
    running = [e["data"] for e in events if e["event_type"] == "tool_started" and e["data"].get("call_id") not in finished]
    if running:
        return f"{running[-1].get('agent_id')} is running {running[-1].get('tool')}{waited}"
    agent = last["data"].get("agent_id")
    if last["event_type"] in ("turn_started", "tool_completed", "tool_failed", "tool_skipped",
                              "rate_limited", "call_retry"):
        return f"{agent} is waiting for the model{waited}"
    if last["event_type"] == "agent_reply" and last["data"].get("tool_calls"):
        return f"{agent} is about to run its tool calls{waited}"
    return f"between steps{waited}"


# -- filters -----------------------------------------------------------------------

def filter_steps(steps: list[Step], *, agents=None, groups=None, tools=None, rounds=None, text: str = "") -> list[Step]:
    """Steps that pass every filter. Steps of no agent (rounds, start, end) are kept by the agent filter."""
    kinds = {kind for group in groups for kind in GROUPS[group]} if groups is not None else None
    needle = text.strip().lower()
    kept = []
    for step in steps:
        if agents is not None and step.agent is not None and step.agent not in agents:
            continue
        if kinds is not None and step.kind not in kinds:
            continue
        if tools is not None and step.kind == "tool" and step.tool not in tools:
            continue
        if rounds is not None and step.round is not None and not rounds[0] <= step.round <= rounds[1]:
            continue
        if needle and needle not in step.search_text():
            continue
        kept.append(step)
    return kept


# -- downloads ---------------------------------------------------------------------

def header_lines(info: dict) -> list[str]:
    """Trial facts for the top of a download: scenario, batch, status, tokens, duration."""
    parts = [f"{key}: {info[key]}" for key in ("scenario", "batch", "status", "stop reason", "tokens", "duration")
             if info.get(key) not in (None, "")]
    return parts


def _fence(text: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}\n{text}\n{ticks}"


def script_markdown(steps: list[Step], info: dict) -> str:
    lines = [f"# {info.get('title', 'Trial')}", ""]
    lines += [f"- {line}" for line in header_lines(info)] + [""]
    for step in steps:
        stamp = clock(step.elapsed)
        if step.kind == "round":
            lines += [f"## {step.title} ({step.meta})", ""]
            continue
        lines.append(f"**{stamp} · {step.title}**" + (f" · {step.meta}" if step.meta else ""))
        lines.append("")
        for section in step.sections:
            if section.label:
                lines.append(f"*{section.label}*")
            lines += [_fence(section.body), ""]
    return "\n".join(lines).rstrip() + "\n"


COLORS = ["#1c64f2", "#0e9f6e", "#7e3af2", "#d97706", "#e02424", "#0694a2", "#6b7280"]


def script_html(steps: list[Step], info: dict) -> str:
    agents = sorted({step.agent for step in steps if step.agent})
    color = {agent: COLORS[i % len(COLORS)] for i, agent in enumerate(agents)}
    esc = html.escape
    out = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>{esc(info.get('title', 'Trial'))}</title>",
        "<style>body{font:15px/1.5 system-ui,sans-serif;max-width:960px;margin:2em auto;padding:0 1em;color:#111}"
        "h2{margin-top:1.6em;border-bottom:1px solid #ddd}.step{border:1px solid #e5e7eb;border-radius:8px;"
        "padding:.6em .9em;margin:.6em 0}.time{color:#6b7280;font-family:monospace}.agent{color:#fff;"
        "border-radius:4px;padding:0 .4em;font-weight:600}.meta{color:#6b7280}.label{color:#374151;"
        "font-style:italic;margin-top:.4em}pre{white-space:pre-wrap;background:#f9fafb;padding:.5em;"
        "border-radius:6px;margin:.2em 0}.error pre{background:#fdecec}.warning pre{background:#fff7e6}"
        "</style></head><body>",
        f"<h1>{esc(info.get('title', 'Trial'))}</h1><ul>",
        *(f"<li>{esc(line)}</li>" for line in header_lines(info)),
        "</ul>",
    ]
    for step in steps:
        if step.kind == "round":
            out.append(f"<h2>{esc(step.title)} <span class='meta'>({esc(step.meta)})</span></h2>")
            continue
        badge = (f"<span class='agent' style='background:{color[step.agent]}'>{esc(step.agent)}</span> "
                 if step.agent else "")
        title = step.title[len(step.agent):].lstrip() if step.agent and step.title.startswith(step.agent) else step.title
        out.append(f"<div class='step'><span class='time'>{esc(clock(step.elapsed))}</span> {badge}"
                   f"<b>{esc(title)}</b>" + (f" <span class='meta'>· {esc(step.meta)}</span>" if step.meta else ""))
        for section in step.sections:
            out.append(f"<div class='{esc(section.style)}'>")
            if section.label:
                out.append(f"<div class='label'>{esc(section.label)}</div>")
            out.append(f"<pre>{esc(section.body)}</pre></div>")
        out.append("</div>")
    out.append("</body></html>")
    return "\n".join(out)


# -- one trial folder --------------------------------------------------------------

def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def trial_info(trial_dir: Path) -> dict:
    """The facts at the top of a download, read from the trial and batch folders."""
    manifest = _read(trial_dir / "manifest.json") or {}
    result = _read(trial_dir / "result.json")
    spec = _read(trial_dir.parent / "batch.json") or {}
    scenario = manifest.get("scenario_name") or spec.get("scenario_name", "?")
    total = ((result or {}).get("tokens") or {}).get("total")
    index = manifest.get("index") or trial_dir.name.rsplit("_", 1)[-1].lstrip("0")
    return {
        "title": f"Trial {index} · {scenario}",
        "scenario": scenario,
        "batch": trial_dir.parent.name,
        "status": result["status"] if result else "not finished",
        "stop reason": (result or {}).get("stop_reason"),
        "tokens": f"{total:,}" if total else None,
        "duration": f"{result['duration_seconds']} s" if result and result.get("duration_seconds") else None,
    }


def trial_script(trial_dir: Path, fmt: str = "md") -> str:
    """A trial folder's whole script as Markdown ("md") or HTML ("html")."""
    steps = build_script(read_events(trial_dir), _read(trial_dir / "manifest.json"))
    info = trial_info(trial_dir)
    return script_html(steps, info) if fmt == "html" else script_markdown(steps, info)
