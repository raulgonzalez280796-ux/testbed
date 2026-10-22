"""Exactly what each agent will receive, without any API call."""

from __future__ import annotations

import random
from pathlib import Path

from .scenario import Scenario, draw_variables, render, turn_one_text
from .tools.builtin import scenario_tools
from .tools.loader import resolve_agent_tools


def preview(home: Path, scenario: Scenario, seed: int = 0) -> dict:
    """Per agent: system prompt, turn-1 input, turn-2+ input and tool schemas.

    Variables are drawn with `seed`, so random values here are examples only.
    In conversation mode, turns also start with other agents' new messages.
    """
    rng = random.Random(seed)
    values = draw_variables(scenario.variables, rng)
    agents = []
    own_tools = scenario_tools(scenario)
    for spec in scenario.agents:
        tools = resolve_agent_tools(spec.tools, home / "tools", spec.id, own_tools)
        agents.append({
            "id": spec.id,
            "provider": spec.provider,
            "model": spec.model,
            "system_prompt": render(spec.system_prompt, values),
            "turn_one_input": turn_one_text(scenario, spec, values),
            "continuation_input": render(spec.continuation_prompt, values),
            "turns": spec.turns,
            "file_access": {name: sorted(ops) for name, ops in scenario.file_access(spec.id).items()},
            "tools": [
                {"name": t.name, "ref": t.ref, "description": t.description, "parameters": t.parameters}
                for t in tools.values()
            ],
        })
    return {
        "scenario": scenario.name,
        "interaction": scenario.interaction,
        "conversation_format": scenario.conversation_format if scenario.interaction == "conversation" else None,
        "turn_order": scenario.turn_order,
        "schedule_mode": scenario.schedule.mode,
        "rounds": scenario.rounds,
        "example_variables": values,
        "initial_files": {spec.name: render(spec.content, values) for spec in scenario.files},
        "agents": agents,
    }


def preview_text(data: dict) -> str:
    lines = [f"Scenario: {data['scenario']}  ({data['interaction']} mode, {data['rounds']} rounds, "
             f"order {' → '.join(data['turn_order'])}, {data['schedule_mode']})"]
    if data["example_variables"]:
        lines.append(f"Example variables: {data['example_variables']}")
    for agent in data["agents"]:
        lines += ["", f"=== Agent {agent['id']} ({agent['provider']} / {agent['model']}, {agent['turns']} turns) ==="]
        lines += ["--- system prompt ---", agent["system_prompt"] or "(none)"]
        lines += ["--- turn 1 input ---", agent["turn_one_input"]]
        lines += ["--- turn 2+ input ---", agent["continuation_input"] or "(none)"]
        if data["interaction"] == "conversation":
            lines.append(f"(each turn also starts with other agents' new messages as "
                         f"{data['conversation_format']!r})")
        lines.append("--- tools ---")
        for tool in agent["tools"]:
            lines.append(f"{tool['name']}: {tool['description']}  params={tool['parameters'].get('properties', {})}")
        if not agent["tools"]:
            lines.append("(none)")
        if agent["file_access"]:
            lines.append(f"--- file access --- {agent['file_access']}")
    return "\n".join(lines)
