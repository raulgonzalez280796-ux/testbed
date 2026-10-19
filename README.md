# llmArena testbed

Controlled experiments with multiple LLM agents. You describe a **scenario** in one YAML file:
- the agents, each with its own provider, model and prompts;
- the files they share;
- which tools each agent gets;
- how they interact.

Then you run it once, or M times with N trials at a time. Every trial leaves a complete record:
- `manifest.json`: everything the agents were given;
- `events.jsonl.gz`: every model call, with the exact request and raw response, and every tool call;
- `result.json`: status, token use and your checks;
- the final files.

Status: **stage 1, milestone 1**. The engine, CLI and Python API work with Anthropic. OpenAI and Gemini adapters and the UI come next.

## Install

Requires Python 3.11 or newer.

```bash
python3.12 -m venv .venv
```

```bash
.venv/bin/python -m pip install -e .
```

## API keys

Add a key only for the providers you use; one is enough. Keys are read from environment variables first, then from `.env` in the project folder (copy `.env.example`). They are never written into scenarios, logs or results.

| Provider | Variable | Where to create a key |
|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY` | https://platform.claude.com/settings/keys |

Use a separate key with a spending limit for experiments.

```bash
.venv/bin/python -m testbed keys
```

## Run

Preview exactly what each agent will receive (no API call):

```bash
.venv/bin/python -m testbed preview scenarios/help_seeking.yaml
```

Run one trial:

```bash
.venv/bin/python -m testbed run scenarios/help_seeking.yaml
```

Run 30 trials, 10 at a time:

```bash
.venv/bin/python -m testbed run scenarios/help_seeking.yaml --trials 30 --parallel 10
```

Other commands:

| Command | What it does |
|---|---|
| `resume <batch>` | Re-runs the trials that did not complete |
| `cancel <batch>` | Stops a running batch |
| `status <batch>` | Shows a batch's progress |
| `recheck <batch>` | Re-runs checks on saved trials, with no API calls |
| `validate <scenario>` | Checks a scenario file and the code it uses |

From Python or a notebook:

```python
from testbed import run_batch
summary = run_batch("scenarios/help_seeking.yaml", trials=30, parallel=10)
```

## Project folder

| Folder | Contents |
|---|---|
| `scenarios/*.yaml` | Scenarios. `help_seeking.yaml` uses only built-in tools; `help_seeking_v1.yaml` is an exact port of the v1 experiment. |
| `tools/*.py` | Your tools. Scenarios refer to them as `file:function`, e.g. `v1_tools:read_log`. |
| `checks/*.py` | Your checks, also as `file:function`. |
| `runs/<batch>/` | Results: `summary.csv`, plus one `trial_NNNN/` folder per trial. |

Commands use the current folder as the project folder. Set `--home` or `TESTBED_HOME` to use another.

## Writing a tool

```python
from typing import Annotated
from testbed import tool, ToolContext

@tool
def submit_answer(ctx: ToolContext, answer: Annotated[str, "Your final answer."]) -> str:
    """Submit your final answer."""
    ctx.files.write("answer.txt", answer)
    return "Submitted."
```

The model sees the function name, the docstring and a JSON schema built from the type hints. `ctx` is filled in by the testbed and hidden from the model. It gives your tool:

| Attribute | What it is |
|---|---|
| `ctx.agent_id` | The calling agent |
| `ctx.files` | Every workspace file |
| `ctx.state` | A dict shared by all tools in the trial |
| `ctx.variables` | The trial's variable values |
| `ctx.rng` | A seeded random generator |
| `ctx.note(...)` | Writes an entry to the trial log |
| `ctx.stop_trial(reason)` | Ends the trial |
| `ctx.finish_agent()` | Ends this agent's turns |

Raise `ToolError("...")` to send a clean error message back to the model. Tool code runs on your machine with your permissions.

The built-in tools are `list_files`, `read_file`, `write_file` and `append_file`. Each scenario file gives every agent an access list per file: `list`, `read`, `write` or `append`.

## Writing a check

```python
from testbed import check, TrialRecord

@check
def output_matches_token(trial: TrialRecord) -> bool:
    return trial.files.get("output.txt") == trial.variables["token"]
```

`trial.tool_calls(agent=..., tool=...)` and `trial.messages(agent)` give you the ordered trace. Checks run after each completed trial; trials that failed for infrastructure reasons are marked n/a.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -t .
```
