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

Version **0.1.0**: the first version (see [CHANGELOG](CHANGELOG.md)). The engine, CLI, Python API and a local web UI all work with Anthropic, OpenAI and Gemini. Only Anthropic has been tested live so far; the OpenAI and Gemini adapters are tested offline, through their SDKs against recorded responses.

| Guide | Contents |
|---|---|
| [Writing scenarios, tools and checks](docs/authoring.md) | How a trial runs, interaction modes, file access, the tool and check APIs |
| [Scenario reference](docs/scenario-reference.md) | Every field of the scenario file (generated from the code) |
| [What a run leaves on disk](docs/results.md) | Folders, manifest, result and event fields, statuses, loading results in pandas |
| [Troubleshooting](docs/troubleshooting.md) | Keys, rate limits, stopped batches, the UI |
| [Pilot kit](docs/pilot.md) | Tasks and a feedback form for first users |

## Quick start

Requires Python 3.11 or newer. Install the testbed with its UI:

```bash
python3.12 -m venv .venv
```

```bash
.venv/bin/python -m pip install -e ".[ui]"
```

Create a project folder with the example scenarios, then open the UI:

```bash
.venv/bin/python -m testbed init my-lab
```

```bash
.venv/bin/python -m testbed --home my-lab ui
```

The UI opens at http://localhost:8501 (the first load takes about 10 seconds). Then:

1. Add an API key in **Settings**.
2. Open or create a scenario in **Scenarios**.
3. Start it in **Run**.
4. Read the trials in **Results**.

The UI listens only on this computer (127.0.0.1) and has no login, so do not expose it to a network.

## The UI

| Page | What you do there |
|---|---|
| **Scenarios** | Create from a template or start blank. Then, per tab: <ul><li>**General**: interaction mode, turn order, rounds, timeout, shared task.</li><li>**Agents**: provider, model, the three prompts, turns, model calls per turn, tools.</li><li>**Files**: initial content, plus an access grid of agents × list/read/write/append.</li><li>**Variables**, **Checks & budget**.</li><li>**Preview**: exactly what each agent will receive, with no API call.</li></ul> Saving writes `scenarios/<name>.yaml`, the same file the CLI runs. |
| **Tools & checks** | Paste code into the editor or upload a `.py` file. It is validated in a separate process and saved to `tools/` or `checks/`. |
| **Run** | M trials in total, N at a time, an optional seed and label. Batches above 20 trials show a token estimate first. A live progress view has cancel and resume. A batch keeps running if you close the browser. |
| **Results** | A check summary per batch, a filterable trials table, and per trial: <ul><li>a turn-by-turn timeline;</li><li>the final files;</li><li>the checks, which you can re-run with the batch's copy or your current code;</li><li>the raw model calls;</li><li>the manifest and downloads.</li></ul> |
| **Settings** | API keys, saved to `.env` and tested with one small request; rate limits per provider. |

## Templates

`init` copies these into `scenarios/`, `tools/` and `checks/`:

| Template | What it shows |
|---|---|
| `help_seeking` | The v1 experiment built from built-in tools only. A can't finish its task; B could help. Do they use the shared log? |
| `help_seeking_v1` | An exact port of v1: v1's own tools, prompts, model and limits, so results compare with v1 runs. |
| `secret_keeper` | Conversation mode with a researcher tool. A Guard holds a random secret; a Requester tries to get it and submits it with `submit_secret`. |
| `negotiation` | Conversation mode with private per-trial values: each side has a secret price limit drawn at random. Offers and deals happen through tools. |

## API keys

Add a key only for the providers you use; one is enough. Keys are read from environment variables first, then from `.env` in the project folder (copy `.env.example`). They are never written into scenarios, logs or results.

| Provider | Variable | Where to create a key |
|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY` | https://platform.claude.com/settings/keys |
| OpenAI | `OPENAI_API_KEY` | https://platform.openai.com/api-keys |
| Google Gemini | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | https://aistudio.google.com/apikey |

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

**Batches above 20 trials ask for confirmation first.** The testbed projects their token use from at least 3 earlier completed trials of the same scenario. If there aren't 3, it runs a 3-trial pilot first. It then prints the projection (median and p90) and asks before starting; add `--yes` to skip the question.

The scenario's `budget` sets token caps:
- `max_total_tokens` stops launching new trials once the batch reaches the cap, and stops trials still running at cap + 10%.
- `max_trial_tokens` ends a single trial.

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
from testbed import run_batch, ConfirmationRequired

try:
    summary = run_batch("scenarios/help_seeking.yaml", trials=100, parallel=20)
except ConfirmationRequired as needed:
    print(needed)  # the projection; run again with yes=True to start
```

## Rate limits and parallel trials

Trials spend most of their time waiting for the API, so one process runs many at once. In a test with a fake provider, 1000 trials ran in parallel in under 100 MB of memory. In practice the limit is your provider account's rate limits, not your computer.

Set your account's limits in `settings.yaml` in the project folder (each provider's console shows them). Limits apply to the whole account, so more machines do not raise them.

```yaml
rate_limits:
  anthropic:
    requests_per_minute: 4000
    input_tokens_per_minute: 2000000
    max_concurrent: 200   # requests in flight at once (default 100)
```

When a provider answers 429 (rate limited), the call waits as long as the provider asks (`retry-after`) and is retried; each wait is logged as a `rate_limited` event. The testbed also lowers how many requests it keeps in flight, then raises it again slowly. A trial fails as `infra_error` only after the retries run out (`limits.retry_attempts` in the scenario, default 6). After 5 trials in a row fail with the same error, such as a bad key, the batch stops. `summary.json` shows how many requests were in flight at most and how often the provider rate-limited.

## Project folder

| Folder | Contents |
|---|---|
| `.env` | API keys (never commit it; `init` adds it to `.gitignore`) |
| `settings.yaml` | Rate limits per provider (optional) |
| `scenarios/*.yaml` | Scenarios (see Templates above) |
| `tools/*.py` | Your tools. Scenarios refer to them as `file:function`, e.g. `v1_tools:read_log`. |
| `checks/*.py` | Your checks, also as `file:function`. |
| `runs/<batch>/` | Results: `summary.csv`, plus one `trial_NNNN/` folder per trial. |

`python -m testbed init <folder>` creates one. Commands use the current folder as the project folder; set `--home` or `TESTBED_HOME` to use another.

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
