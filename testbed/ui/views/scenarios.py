"""Scenario editor. It edits a working copy of scenarios/<name>.yaml and writes the
file on Save; the YAML file stays the single source of truth for every run.
"""

from __future__ import annotations

import itertools
import json
import shutil
import string
from pathlib import Path

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from testbed import providers
from testbed.batch import validate_code
from testbed.keys import load_keys
from testbed.preview import preview
from testbed.project import (
    copy_template_code, new_from_template, safe_stem, scenario_files, template_names,
    template_scenario, write_scenario,
)
from testbed.scenario import Scenario, load_scenario
from testbed.ui.components.common import (
    custom_checks, flash, home, provider_label, show_flash, tool_options,
)

ED = "editor"
OPS = ["list", "read", "write", "append"]
OTHER_MODEL = "Other model id…"


def new_uid() -> int:
    """Widget-key ids, kept per browser session so a module reload can never reuse one."""
    st.session_state["ed_uid"] = st.session_state.get("ed_uid", 0) + 1
    return st.session_state["ed_uid"]


def unique(ids: list) -> list:
    return list(dict.fromkeys(ids))


# -- working copy ---------------------------------------------------------------

def blank_agent(agent_id: str, available: dict) -> dict:
    provider = next((name for name in providers.provider_names() if name in available), "anthropic")
    info = providers.provider_info(provider)
    return {
        "id": agent_id, "provider": provider,
        "model": info.default_model or (info.models[0] if info.models else ""),
        "params": {"max_tokens": 4096}, "system_prompt": "", "task_prompt": "",
        "continuation_prompt": "Continue.", "turns": 5, "max_model_calls_per_turn": 2,
        "tools": [], "request_timeout_seconds": 60.0, "max_retries": 2,
    }


def blank_scenario(available: dict) -> dict:
    agent = blank_agent("A", available)
    agent["task_prompt"] = "Describe the task for agent A here."
    return {"name": "my-scenario", "agents": [agent]}


def load_into_editor(path: Path | None, data: dict) -> None:
    gen = st.session_state.get("ed_gen", 0) + 1
    st.session_state["ed_gen"] = gen
    state = {
        "path": str(path) if path else None,
        "data": json.loads(json.dumps(data)),
        "agent_uids": [new_uid() for _ in data.get("agents", [])],
        "file_uids": [new_uid() for _ in data.get("files", [])],
        "file_versions": {},
        "grids": {},
    }
    state["saved"] = normalized(state["data"])
    state["variables_df"] = variables_frame(state["data"].get("variables", {}))
    st.session_state[ED] = state


def normalized(data: dict) -> str:
    """Compare scenarios by meaning, not by how the working copy happens to store values."""
    try:
        return json.dumps(Scenario.model_validate(data).model_dump(mode="json"), sort_keys=True)
    except ValidationError:
        return json.dumps(data, sort_keys=True, default=str)


def open_file(path: Path) -> None:
    try:
        scenario = load_scenario(path)
        data = scenario.model_dump(mode="json")
    except Exception as error:  # an invalid file still opens, as raw data, so it can be fixed
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        flash(f"{path.name} has problems: {first_line(error)}", "warning")
    load_into_editor(path, data)


def first_line(error: Exception) -> str:
    return str(error).splitlines()[0][:300]


def key(name: str) -> str:
    return f"e{st.session_state.get('ed_gen', 0)}_{name}"


# -- validation -----------------------------------------------------------------

def describe_errors(error: ValidationError, data: dict) -> list[str]:
    messages = []
    agents = data.get("agents", [])
    for item in error.errors(include_url=False):
        location = list(item["loc"])
        if len(location) >= 2 and location[0] == "agents" and isinstance(location[1], int):
            index = location[1]
            name = agents[index].get("id", index) if index < len(agents) else index
            location = [f"agent {name}"] + location[2:]
        where = " → ".join(str(part) for part in location)
        message = item["msg"].removeprefix("Value error, ")
        messages.append(f"{where}: {message}" if where else message)
    return messages


def validate(project: Path, data: dict) -> tuple[Scenario | None, list[str]]:
    try:
        scenario = Scenario.model_validate(data)
    except ValidationError as error:
        return None, describe_errors(error, data)
    unknown = [agent.provider for agent in scenario.agents if not providers.is_known(agent.provider)]
    if unknown:
        return None, [f"unknown provider(s): {', '.join(sorted(set(unknown)))}"]
    try:
        validate_code(project, scenario)
    except Exception as error:
        return None, [first_line(error)]
    return scenario, []


# -- tabs -----------------------------------------------------------------------

def rename_agent(data: dict, old: str, new: str) -> None:
    order = (data.get("schedule") or {}).get("order")
    if order:
        data["schedule"]["order"] = [new if agent_id == old else agent_id for agent_id in order]
    for spec in data.get("files", []):
        access = spec.get("access") or {}
        if old in access:
            access[new] = access.pop(old)


def general_tab(data: dict) -> None:
    data["name"] = st.text_input("Name", data.get("name", ""), key=key("name"))
    data["description"] = st.text_area("Description (for you; never sent to agents)",
                                       data.get("description", ""), key=key("description"), height=80)
    modes = ["environment", "conversation"]
    data["interaction"] = st.radio(
        "How agents interact", modes, index=modes.index(data.get("interaction", "environment")),
        key=key("interaction"), horizontal=True,
        captions=["Only through files and tools; each agent sees only its own prompts.",
                  "Each turn also shows the other agents' new messages."],
    )
    if data["interaction"] == "conversation":
        data["conversation_format"] = st.text_input(
            "How other agents' messages are shown", data.get("conversation_format", "[{sender}]: {text}"),
            key=key("conversation_format"),
            help="{sender} is the agent id, {text} the message. Use just {text} to hide who sent it.")
    ids = unique(agent.get("id", "") for agent in data.get("agents", []))
    schedule = data.setdefault("schedule", {})
    stored = schedule.get("order")
    current = unique(agent_id for agent_id in (stored or ids) if agent_id in ids)
    current += [agent_id for agent_id in ids if agent_id not in current]
    left, right = st.columns(2)
    chosen = left.multiselect("Turn order in each round", ids, default=current,
                              key=key(f"order_{','.join(ids)}"),
                              help="Every agent takes one turn per round, in this order. To reorder, "
                                   "remove agents and add them back in the order you want.")
    missing = [agent_id for agent_id in ids if agent_id not in chosen]
    order = chosen + missing  # every agent must take turns; anyone left out goes last
    if missing:
        left.caption(f"{', '.join(missing)} {'goes' if len(missing) == 1 else 'go'} last "
                     "(every agent takes a turn).")
    # Keep "no order" (= the agents' listed order) when nothing was set; otherwise store
    # exactly what is shown, so the screen and the file can never disagree.
    schedule["order"] = None if stored is None and order == ids else order
    modes = ["fixed", "random_each_round"]
    schedule["mode"] = right.radio(
        "Order", modes, index=modes.index(schedule.get("mode", "fixed")), key=key("mode"),
        format_func=lambda mode: {"fixed": "Same order every round",
                                  "random_each_round": "Shuffle every round (seeded)"}[mode])
    a, b = st.columns(2)
    rounds = a.number_input("Rounds (0 = the largest agent turn count)", min_value=0,
                            value=int(data.get("max_rounds") or 0), key=key("max_rounds"))
    data["max_rounds"] = rounds or None
    data["timeout_seconds"] = b.number_input("Trial timeout (seconds)", min_value=10.0,
                                             value=float(data.get("timeout_seconds", 720.0)),
                                             step=60.0, key=key("timeout"))
    data["shared_task"] = st.text_area(
        "Shared task (optional: every agent gets this on turn 1, before its own task)",
        data.get("shared_task", ""), key=key("shared_task"), height=100)


def model_picker(agent: dict, uid: int) -> None:
    info = providers.provider_info(agent["provider"])
    options = list(info.models)
    if agent.get("model") and agent["model"] not in options:
        options.append(agent["model"])
    options.append(OTHER_MODEL)
    index = options.index(agent["model"]) if agent.get("model") in options else 0
    choice = st.selectbox("Model", options, index=index, key=key(f"{uid}_model_{agent['provider']}"))
    if choice == OTHER_MODEL:
        agent["model"] = st.text_input("Model id", "", key=key(f"{uid}_model_other_{agent['provider']}"),
                                       placeholder="as listed in the provider's documentation")
    else:
        agent["model"] = choice


def agents_tab(project: Path, data: dict, state: dict) -> None:
    available = load_keys(project)
    options = tool_options(project)
    agents = data.setdefault("agents", [])
    st.caption("Each agent has its own model, prompts and tools, and keeps its own history.")
    while len(state["agent_uids"]) < len(agents):
        state["agent_uids"].append(new_uid())
    for index, (agent, uid) in enumerate(zip(list(agents), list(state["agent_uids"]))):
        with st.expander(f"Agent **{agent.get('id', '?')}** · {agent.get('provider')} / {agent.get('model')}",
                         expanded=len(agents) <= 2, key=key(f"{uid}_panel")):
            top = st.columns([1, 1, 1])
            new_id = top[0].text_input("Id", agent.get("id", ""), key=key(f"{uid}_id"),
                                       help="Letters, digits, _ and -. Shown to other agents in conversation mode.")
            others = {other.get("id") for position, other in enumerate(agents) if position != index}
            if new_id != agent.get("id"):
                if new_id in others:
                    top[0].error(f"Another agent is already called {new_id!r}; pick a different id.")
                else:
                    rename_agent(data, agent.get("id"), new_id)
                    agent["id"] = new_id
            names = providers.provider_names()
            provider = top[1].selectbox(
                "Provider", names, index=names.index(agent["provider"]) if agent.get("provider") in names else 0,
                key=key(f"{uid}_provider"), format_func=lambda name: provider_label(name, available))
            if provider != agent.get("provider"):  # a new provider starts from its default model
                info = providers.provider_info(provider)
                agent["model"] = info.default_model or (info.models[0] if info.models else "")
                agent["provider"] = provider
            with top[2]:
                model_picker(agent, uid)
            agent["system_prompt"] = st.text_area(
                "System prompt (sent with every call)", agent.get("system_prompt", ""),
                key=key(f"{uid}_system"), height=80)
            agent["task_prompt"] = st.text_area(
                "Task prompt (turn 1)", agent.get("task_prompt", ""), key=key(f"{uid}_task"), height=120,
                help="Use {{variable}} to insert a variable's value for each trial.")
            agent["continuation_prompt"] = st.text_area(
                "Continuation prompt (turns 2 and later)", agent.get("continuation_prompt", ""),
                key=key(f"{uid}_continuation"), height=70,
                help="Required in environment mode. In conversation mode it may be empty: "
                     "the other agents' messages are the input.")
            row = st.columns(2)
            agent["turns"] = row[0].number_input("Turns", min_value=1, max_value=1000,
                                                 value=int(agent.get("turns", 10)), key=key(f"{uid}_turns"))
            agent["max_model_calls_per_turn"] = row[1].number_input(
                "Model calls per turn", min_value=1, max_value=100,
                value=int(agent.get("max_model_calls_per_turn", 2)), key=key(f"{uid}_calls"),
                help="A turn ends when the model answers without a tool call, or after this many calls.")
            current = list(agent.get("tools", []))
            labels = {**options, **{ref: f"{ref} (missing)" for ref in current if ref not in options}}
            agent["tools"] = st.multiselect("Tools", list(labels), default=current,
                                            format_func=labels.get, key=key(f"{uid}_tools"))
            if st.toggle("Advanced", key=key(f"{uid}_advanced")):
                params_text = st.text_area(
                    "Provider parameters (JSON)", json.dumps(agent.get("params", {}), indent=1),
                    key=key(f"{uid}_params"), height=100,
                    help='Sent to the provider as-is, e.g. {"max_tokens": 1024}. '
                         '"cache": false turns off prompt caching.')
                try:
                    params = json.loads(params_text or "{}")
                    if not isinstance(params, dict):
                        raise ValueError("expected a JSON object")
                    agent["params"] = params
                except ValueError as error:
                    st.error(f"Parameters are not valid JSON: {error}")
                cells = st.columns(2)
                agent["request_timeout_seconds"] = cells[0].number_input(
                    "Request timeout (s)", min_value=1.0, value=float(agent.get("request_timeout_seconds", 60.0)),
                    key=key(f"{uid}_timeout"))
                agent["max_retries"] = cells[1].number_input(
                    "SDK retries", min_value=0, max_value=10, value=int(agent.get("max_retries", 2)),
                    key=key(f"{uid}_retries"))
            buttons = st.columns(3)
            buttons[0].button("Duplicate", key=key(f"{uid}_dup"), on_click=duplicate_agent,
                              args=(state, index), width="stretch")
            buttons[1].button("Move up", key=key(f"{uid}_up"), on_click=move_agent,
                              args=(state, index), disabled=index == 0, width="stretch")
            buttons[2].button("Remove", key=key(f"{uid}_remove"), on_click=remove_agent,
                              args=(state, index), disabled=len(agents) == 1, width="stretch")
    st.button("Add agent", icon=":material/add:", on_click=add_agent, args=(state, available))


def next_id(data: dict) -> str:
    used = {agent.get("id") for agent in data.get("agents", [])}
    candidates = itertools.chain(string.ascii_uppercase, (f"A{i}" for i in itertools.count(1)))
    return next(candidate for candidate in candidates if candidate not in used)


def add_agent(state: dict, available: dict) -> None:
    data = state["data"]
    data["agents"].append(blank_agent(next_id(data), available))
    state["agent_uids"].append(new_uid())
    if (data.get("schedule") or {}).get("order"):
        data["schedule"]["order"].append(data["agents"][-1]["id"])


def duplicate_agent(state: dict, index: int) -> None:
    data = state["data"]
    copy = json.loads(json.dumps(data["agents"][index]))
    copy["id"] = next_id(data)
    data["agents"].insert(index + 1, copy)
    state["agent_uids"].insert(index + 1, new_uid())
    if (data.get("schedule") or {}).get("order"):
        data["schedule"]["order"].append(copy["id"])


def move_agent(state: dict, index: int) -> None:
    for items in (state["data"]["agents"], state["agent_uids"]):
        items[index - 1], items[index] = items[index], items[index - 1]


def remove_agent(state: dict, index: int) -> None:
    data = state["data"]
    removed = data["agents"].pop(index)["id"]
    state["agent_uids"].pop(index)
    order = (data.get("schedule") or {}).get("order")
    if order:
        data["schedule"]["order"] = [agent_id for agent_id in order if agent_id != removed]
    for spec in data.get("files", []):
        (spec.get("access") or {}).pop(removed, None)


def access_grid(state: dict, spec: dict, uid: int, ids: list[str]) -> dict:
    """Agents x operations grid. Rows are read by position, so any agent id is safe."""
    rows = ["*", *ids]
    grid_key = key(f"{uid}_access_{','.join(ids)}")
    if grid_key not in state["grids"]:
        access = spec.get("access") or {}
        state["grids"][grid_key] = pd.DataFrame({
            "agent": ["All agents" if row == "*" else row for row in rows],
            **{op: [op in access.get(row, []) for row in rows] for op in OPS},
        })
    edited = st.data_editor(
        state["grids"][grid_key], key=grid_key, width="stretch", hide_index=True,
        disabled=["agent"],
        column_config={"agent": st.column_config.TextColumn("agent"),
                       **{op: st.column_config.CheckboxColumn(op) for op in OPS}},
    )
    access = {}
    for position, row in enumerate(rows):
        ops = [op for op in OPS if bool(edited.iloc[position][op])]
        if ops:
            access[row] = ops
    return access


def files_tab(data: dict, state: dict) -> None:
    st.caption("Each trial starts with these files in a fresh workspace. The grid sets what each "
               "agent's built-in file tools may do: list (see the name), read, write, append. "
               "Researcher tools can reach every file through ctx.files.")
    files = data.setdefault("files", [])
    ids = unique(agent.get("id", "") for agent in data.get("agents", []))
    while len(state["file_uids"]) < len(files):
        state["file_uids"].append(new_uid())
    for index, (spec, uid) in enumerate(zip(list(files), list(state["file_uids"]))):
        with st.expander(f"**{spec.get('name') or '(unnamed)'}**", expanded=len(files) <= 3,
                         key=key(f"{uid}_panel")):
            spec["name"] = st.text_input("File name", spec.get("name", ""), key=key(f"{uid}_fname"))
            version = state["file_versions"].get(uid, 0)
            spec["content"] = st.text_area(
                "Initial content ({{variable}} works here too)", spec.get("content", ""),
                key=key(f"{uid}_content_{version}"), height=100)
            upload = st.file_uploader("…or load the content from a file", key=key(f"{uid}_upload_{version}"))
            if upload is not None:
                spec["content"] = upload.getvalue().decode("utf-8", errors="replace")
                state["file_versions"][uid] = version + 1
                st.rerun()
            spec["access"] = access_grid(state, spec, uid, ids)
            st.button("Remove file", key=key(f"{uid}_fremove"), on_click=remove_file, args=(state, index))
    st.button("Add file", icon=":material/add:", on_click=add_file, args=(state,))


def add_file(state: dict) -> None:
    files = state["data"].setdefault("files", [])
    names = {spec.get("name") for spec in files}
    name = next(f"file{i}.txt" for i in itertools.count(1) if f"file{i}.txt" not in names)
    files.append({"name": name, "content": "", "access": {}})
    state["file_uids"].append(new_uid())


def remove_file(state: dict, index: int) -> None:
    state["data"]["files"].pop(index)
    state["file_uids"].pop(index)


VARIABLE_COLUMNS = ["name", "type", "value", "prefix", "nbytes", "min", "max", "options"]


def variables_frame(variables: dict) -> pd.DataFrame:
    rows = []
    for name, spec in variables.items():
        rows.append({
            "name": name, "type": spec.get("type"),
            "value": "" if spec.get("value") is None else json.dumps(spec.get("value")),
            "prefix": spec.get("prefix", ""), "nbytes": spec.get("nbytes", 12),
            "min": spec.get("min"), "max": spec.get("max"),
            "options": ", ".join(str(item) for item in spec.get("options") or []),
        })
    return pd.DataFrame(rows, columns=VARIABLE_COLUMNS).astype(
        {"name": "string", "type": "string", "value": "string", "prefix": "string", "options": "string"})


def parse_scalar(text: str):
    text = str(text).strip()
    try:
        return json.loads(text)
    except ValueError:
        return text


def variables_tab(data: dict, state: dict) -> None:
    st.caption("Values are drawn for each trial from the trial's seed, so a re-run trial gets the "
               "same values. Insert them with {{name}} in prompts and file contents.")
    edited = st.data_editor(
        state["variables_df"], key=key("variables"), num_rows="dynamic", width="stretch",
        column_config={
            "type": st.column_config.SelectboxColumn(
                options=["random_token", "constant", "int_range", "choice"], required=True),
            "value": st.column_config.TextColumn(help="constant: the value (JSON or text)"),
            "prefix": st.column_config.TextColumn(help="random_token: text before the random part"),
            "nbytes": st.column_config.NumberColumn(help="random_token: random bytes (2 hex characters each)",
                                                    min_value=1, max_value=64, step=1),
            "min": st.column_config.NumberColumn(help="int_range: smallest value", step=1),
            "max": st.column_config.NumberColumn(help="int_range: largest value", step=1),
            "options": st.column_config.TextColumn(help="choice: comma-separated options"),
        },
    )
    variables = {}
    for _, row in edited.iterrows():
        name = str(row.get("name") or "").strip()
        kind = row.get("type")
        if not name or pd.isna(kind):
            continue
        spec = {"type": kind}
        if kind == "constant":
            spec["value"] = parse_scalar(row.get("value") or "")
        elif kind == "random_token":
            spec["prefix"] = "" if pd.isna(row.get("prefix")) else str(row.get("prefix"))
            spec["nbytes"] = int(row.get("nbytes") or 12) if not pd.isna(row.get("nbytes")) else 12
        elif kind == "int_range":
            spec["min"] = None if pd.isna(row.get("min")) else int(row.get("min"))
            spec["max"] = None if pd.isna(row.get("max")) else int(row.get("max"))
        elif kind == "choice":
            text = "" if pd.isna(row.get("options")) else str(row.get("options"))
            spec["options"] = [parse_scalar(item) for item in text.split(",") if item.strip()]
        variables[name] = spec
    data["variables"] = variables


def checks_tab(project: Path, data: dict) -> None:
    available = {item["ref"]: f"{item['name']} — {item['description'] or 'no description'}"
                 for item in custom_checks(project)}
    current = list(data.get("checks", []))
    labels = {**available, **{ref: f"{ref} (missing)" for ref in current if ref not in available}}
    data["checks"] = st.multiselect("Checks to run after every trial", list(labels), default=current,
                                    format_func=labels.get, key=key("checks"),
                                    help="Write checks on the Tools & checks page.")
    st.subheader("Budget")
    budget = data.setdefault("budget", {})
    a, b, c = st.columns(3)
    total = a.number_input("Batch token cap (0 = none)", min_value=0, step=100_000,
                           value=int(budget.get("max_total_tokens") or 0), key=key("cap_total"),
                           help="No new trials start once the batch reaches it; running ones stop at the cap + tolerance.")
    trial = b.number_input("Trial token cap (0 = none)", min_value=0, step=10_000,
                           value=int(budget.get("max_trial_tokens") or 0), key=key("cap_trial"),
                           help="A trial ends (stop reason token_cap) when it reaches this.")
    tolerance = c.number_input("Tolerance over the batch cap (%)", min_value=0, max_value=100,
                               value=int(round(100 * budget.get("overrun_tolerance", 0.10))), key=key("tolerance"))
    budget.update(max_total_tokens=total or None, max_trial_tokens=trial or None,
                  overrun_tolerance=tolerance / 100)
    st.subheader("Limits")
    limits = data.setdefault("limits", {})
    a, b, c, d = st.columns(4)
    limits["max_tool_input_bytes"] = a.number_input("Tool input (bytes)", min_value=1,
                                                    value=int(limits.get("max_tool_input_bytes", 16_000)), key=key("lim_input"))
    limits["max_file_bytes"] = b.number_input("File size (bytes)", min_value=1,
                                              value=int(limits.get("max_file_bytes", 64_000)), key=key("lim_file"))
    limits["tool_timeout_seconds"] = c.number_input("Tool timeout (s)", min_value=1.0,
                                                    value=float(limits.get("tool_timeout_seconds", 30.0)), key=key("lim_tool"))
    limits["retry_attempts"] = d.number_input("Retries after API errors", min_value=0, max_value=20,
                                              value=int(limits.get("retry_attempts", 6)), key=key("lim_retry"))


def preview_tab(project: Path, scenario: Scenario | None, errors: list[str]) -> None:
    if scenario is None:
        st.info("Fix the problems listed above to see the preview.")
        return
    data = preview(project, scenario)
    st.caption("Exactly what each agent receives, with example variable values. Nothing is sent to any API.")
    if data["example_variables"]:
        st.markdown("Example variables: " + ", ".join(f"`{k}` = `{v}`" for k, v in data["example_variables"].items()))
    if data["interaction"] == "conversation":
        st.info(f"Conversation mode: every turn also starts with the other agents' new messages, "
                f"shown as `{data['conversation_format']}`.")
    for agent in data["agents"]:
        with st.container(border=True):
            st.markdown(f"#### {agent['id']} · {agent['provider']} / {agent['model']} · {agent['turns']} turns")
            st.markdown("**System prompt**")
            st.code(agent["system_prompt"] or "(none)", language=None, wrap_lines=True)
            st.markdown("**Turn 1**")
            st.code(agent["turn_one_input"] or "(only the other agents' messages)", language=None, wrap_lines=True)
            st.markdown("**Turns 2+**")
            st.code(agent["continuation_input"] or "(none)", language=None, wrap_lines=True)
            st.markdown("**Tools the model sees**")
            if agent["tools"]:
                st.json({tool["name"]: {"description": tool["description"], "parameters": tool["parameters"]}
                         for tool in agent["tools"]}, expanded=False)
            else:
                st.caption("none")
            if agent["file_access"]:
                st.markdown("**File access** " + ", ".join(
                    f"`{name}`: {'/'.join(ops)}" for name, ops in agent["file_access"].items()))


# -- page -------------------------------------------------------------------------

def has_comments(path: Path) -> bool:
    try:
        return any(line.lstrip().startswith("#") for line in path.read_text(encoding="utf-8").splitlines())
    except OSError:
        return False


def save(project: Path, target: Path, scenario: Scenario) -> None:
    """Write the scenario; keep a .bak of a hand-written file with comments the first time."""
    backup = target.with_name(target.name + ".bak")
    note = ""
    if target.exists() and has_comments(target) and not backup.exists():
        shutil.copy2(target, backup)
        note = f" Your original, with its comments, is kept as scenarios/{backup.name}."
    write_scenario(target, scenario)
    copy_template_code(project, scenario)
    open_file(target)
    flash(f"Saved scenarios/{target.name}.{note}")

def toolbar(project: Path) -> None:
    files = scenario_files(project)
    state = st.session_state.get(ED)
    current = Path(state["path"]).name if state and state.get("path") else None
    names = [path.name for path in files]
    left, middle, right = st.columns([3, 2, 2], vertical_alignment="bottom")
    with left:
        index = names.index(current) if current in names else None
        picked = st.selectbox("Open a scenario", names, index=index, placeholder="Choose a scenario file",
                              key=f"pick_{st.session_state.get('ed_gen', 0)}")
        if picked and picked != current:
            open_file(project / "scenarios" / picked)
            st.rerun()
    with middle.popover("New from template", icon=":material/library_add:", width="stretch"):
        templates = template_names()
        template = st.selectbox("Template", templates,
                                format_func=lambda name: template_scenario(name).name)
        st.caption(template_scenario(template).description)
        stem = st.text_input("File name", template, key="template_stem")
        if st.button("Create", type="primary"):
            try:
                path = new_from_template(project, template, stem)
            except FileExistsError as error:
                st.error(str(error))
            else:
                open_file(path)
                flash(f"Created scenarios/{path.name} from the {template} template.")
                st.rerun()
    with right.popover("New blank", icon=":material/add:", width="stretch"):
        st.caption("One agent to start with; add more in the Agents tab.")
        if st.button("Create blank scenario", type="primary"):
            load_into_editor(None, blank_scenario(load_keys(project)))
            st.rerun()


def render() -> None:
    project = home()
    st.title("Scenarios")
    show_flash()
    toolbar(project)
    state = st.session_state.get(ED)
    if state is None:
        files = scenario_files(project)
        if files:
            open_file(files[0])
            st.rerun()
        st.info("No scenarios yet. Create one from a template (the v1 experiment is "
                "**help-seeking**) or start blank.")
        return
    data = state["data"]
    tabs = st.tabs(["General", "Agents", "Files", "Variables", "Checks & budget", "Preview"])
    with tabs[0]:
        general_tab(data)
    with tabs[1]:
        agents_tab(project, data, state)
    with tabs[2]:
        files_tab(data, state)
    with tabs[3]:
        variables_tab(data, state)
    with tabs[4]:
        checks_tab(project, data)
    scenario, errors = validate(project, data)
    with tabs[5]:
        preview_tab(project, scenario, errors)

    with st.sidebar:
        st.divider()
        path = Path(state["path"]) if state.get("path") else None
        target = path or project / "scenarios" / f"{safe_stem(data.get('name') or 'scenario')}.yaml"
        dirty = normalized(data) != state["saved"]
        st.markdown(f"**Editing** `scenarios/{target.name}`" + (" · :orange[unsaved changes]" if dirty else ""))
        if errors:
            st.error(f"{len(errors)} problem(s) to fix before saving:\n\n" + "\n".join(f"- {e}" for e in errors))
        else:
            st.success("Valid scenario.")
        if st.button("Save", type="primary", disabled=bool(errors), width="stretch"):
            if path is None and target.exists():
                st.error(f"scenarios/{target.name} already exists; change the name or use Save as copy.")
            else:
                save(project, target, scenario)
                st.rerun()
        with st.popover("Save as copy", width="stretch", disabled=bool(errors)):
            stem = st.text_input("New file name", f"{target.stem}_copy", key=key("copy_stem"))
            copy_target = project / "scenarios" / f"{safe_stem(stem)}.yaml"
            if st.button("Save copy", type="primary", key=key("copy_save")):
                if copy_target.exists():
                    st.error(f"scenarios/{copy_target.name} already exists.")
                else:
                    save(project, copy_target, scenario)
                    st.rerun()
        if st.button("Discard changes", disabled=not dirty or path is None, width="stretch",
                     help="Reopen the file as it is on disk."):
            open_file(path)
            flash("Changes discarded.", "info")
            st.rerun()
        if st.button("Run this scenario", disabled=bool(errors) or dirty or path is None, width="stretch",
                     help="Save first."):
            st.session_state["run_scenario"] = target.name
            st.switch_page(st.session_state["pages"]["run"])
        if path is not None:
            with st.popover("Delete scenario", width="stretch"):
                st.write(f"Delete `scenarios/{path.name}`? Results already run are kept.")
                if st.button("Delete", type="primary"):
                    path.unlink()
                    st.session_state.pop(ED, None)
                    flash(f"Deleted scenarios/{path.name}.", "info")
                    st.rerun()
        st.caption("Saving rewrites the YAML file in a standard layout. Comments are not kept; "
                   "a file with comments is first copied to <name>.yaml.bak.")
