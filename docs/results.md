# What a run leaves on disk

Every batch is one self-contained folder under `runs/` in your project. It holds a frozen copy of the scenario and of your tool and check code, so a batch can be read, re-checked or resumed later, even after you change the scenario. The UI's Results page reads these files; you can also open them directly, or load them into pandas.

```
runs/<batch_id>/
  batch.json            settings of the batch
  scenario.yaml         the scenario exactly as it ran
  code/tools/*.py       the tool code it used (frozen)
  code/checks/*.py      the check code it used (frozen)
  summary.json          progress and one row per trial
  summary.csv           the same rows, for spreadsheets and pandas
  runner.log            output of the runner process (batches started from the UI)
  trial_0001/
    manifest.json       everything the agents were given
    events.jsonl.gz     every event, in order
    result.json         status, stop reason, tokens, checks
    workspace/          the final files
  trial_0002/ …
```

Other entries you may see:

| Entry | What it is |
|---|---|
| `trial_NNNN.attempt1` | An earlier attempt of a trial that didn't finish (for example, killed); kept when the trial is re-run |
| `cancel` | Asks the running batch to stop (written by Cancel in the UI, or `python -m testbed cancel`) |
| `code/checks.before-<time>` | The previous check code, kept when you re-run checks with your project's current code |
| `runner.pid` | The process id of the runner, for the UI |

## Statuses

A trial's `status` says whether it produced a result. Check values are only computed for `completed` and `timeout`. Every other status means the trial is not a behavioral result, and it is never counted as one.

| Status | Meaning |
|---|---|
| `completed` | Ran to a normal end (see stop reasons) |
| `timeout` | Ran longer than the scenario's `timeout_seconds` |
| `infra_error` | A model call kept failing (rate limit, server, network, bad key), or the testbed hit an unexpected error. `error` says which. |
| `budget_stopped` | Stopped because the batch reached its token cap plus tolerance |
| `cancelled` | Stopped because the batch was cancelled |

`summary.json` also uses these for trials that have not finished:

| Status | Meaning |
|---|---|
| `queued` | Not started yet |
| `running` | Running now |
| `incomplete` | Started, but no result (the run was interrupted); resume re-runs it |
| `not_run` | Never started, because the batch stopped (token cap, or repeated identical errors) |

Stop reasons are listed in [authoring](authoring.md#when-a-trial-ends).

## batch.json

| Field | |
|---|---|
| `batch_id` | Folder name: UTC time plus a random suffix |
| `label` | Your label, if any |
| `scenario_name`, `scenario_file`, `scenario_hash` | Which scenario. The hash changes whenever anything in the scenario changes. |
| `trials`, `parallel` | M and N |
| `seed` | The batch seed. Each trial's seed is derived from it and the trial number. |
| `created_at`, `testbed_version` | |
| `projection` | The token estimate shown before the batch started, if there was one |

## summary.json and summary.csv

`summary.json` has these batch-level fields:

| Field | Contents |
|---|---|
| `status` | `created`, `running`, `finished`, `cancelled`, `budget_stopped`, or `stopped` (repeated identical errors) |
| `message` | Why the batch stopped early, if it did |
| `counts` | Trials per status |
| `tokens_total`, `budget`, `projection` | Token use and limits |
| `running_peak` | The most trials that ran at once |
| `rate_limits` | Per provider: requests sent, rate-limited responses, most requests in flight, the lowest in-flight cap after backing off, how many requests waited and their mean wait |
| `started_at`, `finished_at` | |
| `trials` | One row per trial |

Each row has `trial`, `status`, `stop_reason`, `duration_seconds`, `tokens`, `error` (the error kind, if any) and `dir`, plus one `check:<file>:<function>` column per check. `summary.csv` has the same rows, with relative paths only.

## manifest.json (per trial)

Written before the trial starts. It records everything that shaped what the agents saw.

| Field | Contents |
|---|---|
| `trial_id`, `batch_id`, `index`, `seed`, `started_at` | Identity |
| `scenario_name`, `scenario_hash`, `scenario` | The full scenario |
| `interaction`, `rounds`, `timeout_seconds`, `limits`, `budget` | Settings in effect |
| `variables` | This trial's values |
| `initial_files` | Each file's content after variables were filled in |
| `agents` | Per agent: provider, model, params, turns, calls per turn, timeouts and retries; the **rendered** system prompt, turn-1 input and continuation prompt; each tool's `ref`, name, description and JSON schema **as sent**, the SHA-256 of its source file, and its `file` and `op` if it declares them; and the adapter class and SDK version |
| `code_sha256` | SHA-256 of every frozen code file in the batch |
| `testbed_version`, `python`, `platform`, `packages` | Software versions |

API keys are never written here, or anywhere else in a run.

## result.json (per trial)

| Field | Contents |
|---|---|
| `status`, `stop_reason` | See above |
| `error` | For `infra_error`: `{kind, type, status_code, message}` (the message is redacted) |
| `started_at`, `finished_at`, `duration_seconds` | |
| `rounds_completed` | Rounds fully finished |
| `turns` | Turns taken, per agent |
| `tokens` | `input_tokens` (including cached), `output_tokens` (including reasoning), `cached_input_tokens`, `cache_write_tokens`, `reasoning_tokens` and `total`, plus the same per agent under `per_agent` |
| `checks` | `check reference → value` |
| `check_errors` | `check reference → error`, for checks that raised |
| `checks_not_applicable` | `true` when checks were skipped (status other than completed or timeout) |
| `rechecked_at` | When checks were last re-run |

## events.jsonl.gz (per trial)

One JSON object per line, gzip-compressed once the trial finishes. Each line holds `run_id` (batch/trial), `seq` (1, 2, 3, …), a UTC `timestamp`, the `event_type`, and its `data`.

Most events also carry `agent_id`, `round` and `turn`; model and tool events add `call` or `call_id`.

| Event | Data | When |
|---|---|---|
| `run_started` | `agents`, `seed` | Start of the trial |
| `round_started` | `round`, `order` | Each round, with the turn order used |
| `turn_started` | `prompt` | Start of a turn: the exact input sent to the agent |
| `turn_skipped` | `reason` | Conversation mode, when an agent has no input this turn |
| `model_call` | `provider`, `duration_seconds`, `attempts`, `request_id`, `model`, `request`, `response` | After each successful model call: **the request exactly as passed to the SDK, and the raw response** |
| `agent_reply` | `call`, `text`, `tool_calls` (`id`, `name`, `arguments`), `stop_reason`, `truncated`, `usage` | The parsed reply of each model call |
| `empty_reply` | `call`, `stop_reason` | A reply with no content (not added to the history) |
| `rate_limited` / `call_retry` | `provider`, `attempt`, `delay_seconds`, `error_kind`, `status_code`, `message` | A model call is retried: after a 429, or after a server, network or timeout error |
| `model_call_failed` | `provider`, `duration_seconds`, `attempts`, `request`, `error_kind`, `error_type`, `status_code`, `message` | A model call failed for good; the trial ends as `infra_error` |
| `tool_started` | `call_id`, `tool`, `arguments`, and `file`, `op` when known | Before each tool call. `file` and `op` (read, write, append, list) are set for tools that declare them with `@tool(file=..., op=...)`, as the predefined tools do |
| `tool_completed` | `call_id`, `tool`, `result` (and `file`, `op`) | The tool returned `result` |
| `tool_failed` | `call_id`, `tool`, `error_type`, `error` (and `file`, `op`) | Refused or raised; `error` is what the model was told |
| `tool_skipped` | `call_id`, `tool`, `arguments`, `reason` | Not run because the reply was cut off (`truncated`) |
| `tool_note` | `call_id`, `tool`, `message`, `data` | Written by your tool with `ctx.note(...)` |
| `turn_finished` | `text`, `stop_reason`, `model_calls`, `cap_hit`, `finished` | End of a turn. `text` is the agent's message for the turn; `cap_hit` means the call limit ended it |
| `token_cap_reached` | `tokens`, `cap` | The trial reached `max_trial_tokens` |
| `run_timeout` | `seconds` | The trial timed out |
| `run_cancelled` | | The trial was cancelled |
| `runtime_error` | `error_type`, `message`, `traceback` | An unexpected error inside the testbed (please report it) |
| `run_finished` | `status`, `stop_reason`, `tokens` | End of the trial |

`usage` in `agent_reply` has the same fields as `tokens` in result.json, for that one call.

## Loading results in Python

One row per trial:

```python
import pandas as pd

trials = pd.read_csv("runs/<batch_id>/summary.csv")
completed = trials[trials.status == "completed"]
print(completed["check:help_seeking:counter_valid"].mean())
```

Every event of one trial:

```python
from pathlib import Path
from testbed.logger import read_events

events = read_events(Path("runs/<batch_id>/trial_0001"))
calls = [e["data"] for e in events if e["event_type"] == "tool_started"]
```

For checks over saved trials, `TrialRecord.load(Path(".../trial_0001"))` (from `testbed.checks`) gives the same object your checks receive.
