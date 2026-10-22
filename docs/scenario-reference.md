# Scenario reference

<!-- Generated from testbed/scenario.py by `python -m testbed reference`. Do not edit by hand. -->

A scenario is one YAML file in your project's `scenarios/` folder. The UI's scenario editor writes the same file. Unknown fields are rejected, so a typo is reported instead of ignored. For autocompletion in a code editor, export the JSON Schema with `python -m testbed schema --out scenario.schema.json`.

A small example with two agents on different providers:

```yaml
name: my-experiment
interaction: environment          # or: conversation
variables:
  token: {type: random_token, prefix: arena_}
files:
  - name: notes.txt
    content: "Token: {{token}}"
tools:                            # single-purpose tools: one action on one file
  - {name: read_notes, kind: read_file, file: notes.txt}
  - {name: add_note, kind: append_file, file: notes.txt}
agents:
  - id: A
    provider: anthropic
    model: claude-haiku-5-5
    task_prompt: Read the notes and pass on what they say.
    continuation_prompt: Continue.
    turns: 3
    tools: [read_notes]
  - id: B
    provider: openai
    model: gpt-6-luna
    task_prompt: Summarize what you learn in the notes.
    continuation_prompt: Continue.
    turns: 3
    tools: [read_notes, add_note]
checks: [my_checks:token_was_shared]
budget: {max_trial_tokens: 200000}
```

Rules checked when a scenario is loaded, beyond the field types below:

- Agent ids and file names are unique.
- `schedule.order`, if set, lists every agent exactly once.
- `files[].access` names only existing agents, or `*`.
- Tool names are unique. A read_file, write_file or append_file tool names one of the scenario's files in `file`; a list_files tool may limit itself with `files`.
- Every name in an agent's `tools` is a tool from `tools`, a built-in tool or researcher code (`file:function`). A `tools` entry with the same name as a built-in replaces it.
- Every `{{variable}}` used in a prompt or file content is defined in `variables`.
- In environment mode, every agent has a turn-1 input (`shared_task` or its `task_prompt`), and an agent with more than one turn has a `continuation_prompt`.
- `conversation_format` contains `{text}`.

## Scenario

The top level of the YAML file. A complete experiment setup: one YAML file in scenarios/.

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | text (at least 1) | **required** | Shown in the UI and recorded in every trial. |
| `description` | text | empty | For people; never sent to any agent. |
| `interaction` | one of `environment`, `conversation` | `environment` | environment: agents affect each other only through files and tools. conversation: each turn also starts with the other agents' final texts since this agent's last turn. |
| `conversation_format` | text | `[{sender}]: {text}` | conversation mode: how each message from another agent is shown. {sender} is its agent id, {text} the message; use {text} alone to hide the sender. |
| `schedule` | [Schedule](#schedule) | see below | Turn order. |
| `max_rounds` | integer, optional (≥ 1) | empty | Rounds per trial. Empty means the largest agent turn count. |
| `timeout_seconds` | number (> 0) | `720.0` | A trial still running after this long ends as timeout. |
| `shared_task` | text | empty | Every agent gets this on turn 1, before its own task_prompt. |
| `variables` | map of text → [Variable](#variable) | empty | Per-trial values, inserted with {{name}} in prompts and file contents. |
| `files` | list of [File](#file) | empty | The workspace files every trial starts with. |
| `tools` | list of [Tool](#tool) | empty | Single-purpose tools for this scenario (one action on one file), named by you. Agents list them by name next to built-in and researcher tools. |
| `agents` | list of [Agent](#agent) (at least 1) | **required** | At least one agent. |
| `checks` | list of text | empty | file:function for researcher checks in checks/, run after every trial. |
| `budget` | [Budget](#budget) | see below | Token caps. |
| `limits` | [Limits](#limits) | see below | Tool and retry limits. |

## Agent

Each item of `agents`. One agent: its model, prompts, turns and tools. Each agent keeps its own history.

| Field | Type | Default | Description |
|---|---|---|---|
| `id` | text | **required** | Unique name: a letter, then letters, digits, '_' or '-'. In conversation mode it is shown to the other agents as {sender}. |
| `provider` | text | **required** | anthropic, openai or gemini. |
| `model` | text | **required** | The provider's model id, e.g. claude-haiku-5-5. |
| `params` | map of text → any | empty | Sent to the provider as-is, e.g. max_tokens (all providers), output_config (Anthropic), reasoning (OpenAI), thinking_config (Gemini). The one testbed key is cache (default true): provider prompt caching. |
| `system_prompt` | text | empty | Sent with every model call. |
| `task_prompt` | text | empty | The agent's turn-1 input, after the scenario's shared_task. |
| `continuation_prompt` | text | empty | The input for turns 2 and later. Required in environment mode; in conversation mode it may be empty, so the other agents' new messages are the whole input. |
| `turns` | integer (≥ 1, ≤ 1000) | `10` | How many turns this agent takes; it is skipped in later rounds. |
| `max_model_calls_per_turn` | integer (≥ 1, ≤ 100) | `2` | A turn ends when the model answers without a tool call, or after this many calls. Tool results from the last call are seen next turn. |
| `tools` | list of text | empty | Built-in tool names (list_files, read_file, write_file, append_file), or file:function for researcher tools in tools/. |
| `request_timeout_seconds` | number (> 0) | `60.0` | Timeout for one model request. |
| `max_retries` | integer (≥ 0, ≤ 10) | `2` | Quick retries inside the provider's SDK, per request. |

## File

Each item of `files`. A file in the workspace. Every trial starts with a fresh copy of each file.

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | text | **required** | File name, without folders: letters, digits, '.', '_' and '-'. |
| `content` | text | empty | Initial content. {{variable}} is replaced with the trial's value. |
| `access` | map of text → list of one of `list`, `read`, `write`, `append` | empty | What each agent's built-in file tools may do with this file: agent id (or '*' for every agent) -> a list of list, read, write, append. 'list' alone shows the name in list_files without allowing reads or writes. Researcher tools reach every file through ctx.files regardless. |

## Tool

Each item of `tools`. A single-purpose tool for this scenario: one action on one file, under your own name. It takes no file name, so it can never reach another file; giving it to an agent is the permission.

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | text | **required** | The name the model sees, e.g. read_log: a letter, then letters, digits or '_'. Agents list it in their tools. |
| `kind` | one of `read_file`, `write_file`, `append_file`, `list_files` | **required** | read_file returns the file; write_file replaces it (argument: content); append_file adds text and a newline (argument: text); list_files lists file names. |
| `file` | text, optional | empty | read_file, write_file, append_file: the one file this tool works on. |
| `files` | list of text, optional | empty | list_files: the file names it may show. Empty means every file in the workspace. |
| `description` | text | empty | What the model is told the tool does. Empty means a standard description, e.g. 'Read the UTF-8 contents of log.txt.' |

## Variable

Each value of `variables` (the key is the variable's name). A value drawn for each trial from the trial's seed, so a re-run trial gets the same value.

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | one of `constant`, `random_token`, `int_range`, `choice` | **required** | How the value is chosen. |
| `value` | any | empty | constant: the value. |
| `prefix` | text | empty | random_token: text before the random hex part. |
| `nbytes` | integer (≥ 1, ≤ 64) | `12` | random_token: random bytes; the token has 2 hex characters per byte. |
| `min` | integer, optional | empty | int_range: the smallest value (inclusive). |
| `max` | integer, optional | empty | int_range: the largest value (inclusive). |
| `options` | list of any, optional | empty | choice: the values to pick from. |

## Schedule

`schedule`. Who takes turns, in what order.

| Field | Type | Default | Description |
|---|---|---|---|
| `order` | list of text, optional | empty | Turn order in each round; must list every agent once. Empty means the order the agents are listed in. |
| `mode` | one of `fixed`, `random_each_round` | `fixed` | fixed: the same order every round. random_each_round: shuffled every round from the trial's seed. |

## Budget

`budget`. Token caps. Token use is always recorded, with or without caps.

| Field | Type | Default | Description |
|---|---|---|---|
| `max_total_tokens` | integer, optional (≥ 1) | empty | Cap for the whole batch: no new trials start once it is reached; running trials stop at the cap plus the tolerance (status budget_stopped). |
| `max_trial_tokens` | integer, optional (≥ 1) | empty | Cap for one trial: it ends with stop reason token_cap. |
| `overrun_tolerance` | number (≥ 0, ≤ 1) | `0.1` | How far running trials may go past max_total_tokens (0.10 = 10%). |

## Limits

`limits`. Safety limits for tools and retries.

| Field | Type | Default | Description |
|---|---|---|---|
| `max_tool_input_bytes` | integer (≥ 1) | `16000` | Largest tool-call input; larger calls return an error. |
| `max_file_bytes` | integer (≥ 1) | `64000` | Largest workspace file the file tools will read or write. |
| `tool_timeout_seconds` | number (> 0) | `30.0` | A tool call that takes longer returns a timeout error. |
| `retry_attempts` | integer (≥ 0, ≤ 20) | `6` | Retries after rate limits, server or connection errors, on top of the SDK's own; the trial fails as infra_error after the last one. |
| `retry_max_delay_seconds` | number (> 0) | `60.0` | Longest wait between retries when the provider gives no retry-after. |
