# Writing scenarios, tools and checks

A **scenario** is one YAML file in your project's `scenarios/` folder. The UI's Scenarios page edits the same file; the CLI and the Python API run it. Every field is listed in the [scenario reference](scenario-reference.md). This guide explains how the pieces behave together.

The four templates (`python -m testbed init` copies them into a project) are worked examples:

| Template | Shows |
|---|---|
| `help_seeking` | environment mode, predefined tools that each work on one file (`check_env`, `read_log`, `write_log`, …) |
| `help_seeking_v1` | the same kind of tools under v1's exact names, descriptions and schemas, with v1's prompts |
| `secret_keeper` | conversation mode, a tool that ends the trial (`submit_secret`), a hidden sender (`conversation_format: "{text}"`) |
| `negotiation` | conversation mode, private per-trial values, shared tool state (`ctx.state`) |

## How a trial runs

1. **Workspace.** A fresh folder is created with every file in `files`. `{{variable}}` in a file's content is replaced with this trial's value.
2. **Variables.** Each variable is drawn from the trial's seed, which comes from the batch seed and the trial number. So trial 7 of a batch always gets the same values, including when it is resumed.
3. **Rounds.** The trial runs up to `max_rounds` rounds; by default, the largest `turns` of any agent. In each round, every agent that still has turns takes one, in `schedule.order`, or shuffled each round if `schedule.mode` is `random_each_round` (the shuffle also comes from the trial's seed).
4. **A turn.** An agent's turn starts with one input message, then makes up to `max_model_calls_per_turn` model calls:
   - The input on **turn 1** is `shared_task` followed by the agent's `task_prompt`, separated by a blank line. On **later turns** it is the agent's `continuation_prompt`.
   - In **conversation mode**, the input first lists every *other* agent's messages since this agent's last turn, each formatted with `conversation_format` (placeholders: `{sender}`, `{text}`, `{round}`), then the prompt. An agent whose input would be empty skips that turn (`turn_skipped` event).
   - If the model answers **without a tool call**, the turn ends.
   - If it **calls tools**, they run in order, their results are added to the history, and the model is called again.
   - After the last allowed call the turn ends even if tools were called. The agent sees those tool results at the start of its next turn, together with its next input.
5. **What an agent "says".** The texts of all model calls in a turn, joined together, are the agent's message for that turn. In conversation mode that is what the other agents see. Tool calls and tool results are never shown to other agents.
6. **Histories.** Every agent keeps its own history for the whole trial. Agents never see each other's prompts or reasoning. Provider-specific content (for example Claude's thinking blocks) is sent back unchanged on the next call.

### When a trial ends

| Stop reason | When |
|---|---|
| `round_limit` | All rounds ran (the normal end) |
| `all_agents_done` | Every agent used its turns, or called `ctx.finish_agent()`, before the last round |
| a tool's reason | A tool called `ctx.stop_trial("reason")` (the default reason is `stopped_by_tool`) |
| `token_cap` | The trial reached `budget.max_trial_tokens` |
| `timeout` | The trial ran longer than `timeout_seconds` (status `timeout`) |
| `infra_error`, `runtime_error` | A model call failed after its retries, or the testbed hit an unexpected error (status `infra_error`) |
| `budget_stopped` | The batch reached `budget.max_total_tokens` plus its tolerance |
| `cancelled` | The batch was cancelled |

## Interaction modes

**environment** (default): agents influence each other only through what they do: files, and any shared state your tools keep. Each agent's input is only its own prompts. Use it when communication should be possible but not handed to the agents.

**conversation**: every turn also delivers the other agents' new messages, as described above. `continuation_prompt` may be empty, so the conversation itself is the input. An agent's `task_prompt` may also be empty if it should start by answering. The agent id is the sender name; use `conversation_format: "{text}"` to hide who is speaking.

The testbed adds no instructions of its own, so **tell each agent in its prompt that what it writes is sent to the others** (for example: "everything you write is sent to the buyer, and their messages will appear here"). Without that, an agent that speaks first may conclude it has no way to communicate, as an early live run of `secret_keeper` showed.

## Tools

Every tool is a Python file in your project's `tools/` folder, holding one function marked `@tool` that has the same name as the file: `tools/read_log.py` is the tool `read_log`. An agent's `tools` list names the tools it may call:

```yaml
agents:
  - id: A
    tools: [check_env, read_log, write_log]
```

The model sees each tool's name, description and arguments, nothing else. **Giving a tool to an agent is the permission**; there is no separate access table.

### The Tools page

- **The list** shows every tool in `tools/`:
  - its check status: ✓ ok, ⚠ warnings, ✗ errors;
  - your note;
  - what the model is told (the docstring's first line);
  - the file it works on;
  - which scenarios use it, and for which agents.
- **Add tool** starts from one of three things. Whichever you use, the tool's name becomes its file name.
  - a **starter**: read a file, write a file, append to a file, list the files, or blank;
  - **pasted code**;
  - an **uploaded `.py` file**.
- **Opening a tool** shows its code in an editor, with these actions:
  - **Check** runs the [code check](#the-code-check) without saving.
  - **Save** checks first. Errors stop the save; warnings don't.
  - **Your note** is stored in `tools/notes.yaml` and never sent to any model.
  - **Duplicate** copies the tool under a new name and renames the function.
  - **Delete** first says which scenarios use the tool.

In the scenario editor, the Agents tab picks from the same list, labeled with your notes.

### Predefined tools

`python -m testbed init <project>` copies these into `tools/`. They are ordinary tools: open, edit, copy or delete them like your own.

| Tool | Does | The model fills in | Used in |
|---|---|---|---|
| `check_env` | Lists every file in the workspace | nothing | help_seeking |
| `list_files` | The same, under v1's name | nothing | help_seeking_v1 |
| `read_log` | Returns log.txt | nothing | both help-seeking templates |
| `write_log` | Adds the text and a newline to log.txt | `text` | help_seeking |
| `append_log` | The same, under v1's name | `text` | help_seeking_v1 |
| `read_output`, `write_output` | Return or replace output.txt | `content`, to write | both help-seeking templates |
| `read_counter`, `write_counter` | Return or replace counter.txt | `content`, to write | both help-seeking templates |
| `make_offer`, `accept_offer` | Record an offer in shared state; accept the other side's offer, write deal.txt and end the trial | `price`, to offer | negotiation |
| `submit_secret` | Checks a guess; a correct guess ends the trial | `guess` | secret_keeper |

The file tools send the model exactly the descriptions and JSON schemas v1 sent, and `help_seeking_v1` also uses v1's names, so it reproduces v1's stimulus.

### Writing a tool

```python
from testbed import ToolContext, tool


@tool(file="log.txt", op="append")
def write_log(ctx: ToolContext, text: str) -> str:
    """Append text followed by a newline to log.txt."""
    ctx.files.append("log.txt", text + "\n")
    return "Text appended."
```

What the model sees:
- **Name:** the function name, which is also the file name.
- **Description:** the docstring (or `@tool(description=...)`).
- **Arguments:** a JSON schema built from the type hints. `Annotated[type, "text"]` adds a description to an argument; defaults make an argument optional.
- **Schema override:** `@tool(schema={...})` replaces the generated schema exactly, while arguments are still checked against the type hints. The predefined file tools use it to send v1's schemas.

**One file per tool: `file=` and `op=`.** Both are optional.
- **`file="log.txt"`** limits the tool to that file. Inside the tool, `ctx.files` works only on log.txt. Any other name raises an error that goes back to the model, for example "The tool write_log works only on log.txt, not 'x.txt'". `ctx.files.list()` shows only log.txt. So "this tool touches only log.txt" is enforced, not just promised.
- **`op=`** (read, write, append or list) is recorded with every call, together with the file. Checks can then ask "did A append to log.txt?" whatever the tool is called (see `trial.tool_calls` below).
- A tool without `file=` reaches every workspace file through `ctx.files`, so your code decides. `make_offer` keeps its state in `ctx.state` instead:

```python
from typing import Annotated

from testbed import ToolContext, ToolError, tool


@tool
def make_offer(ctx: ToolContext, price: Annotated[int, "Your offer, in whole dollars."]) -> str:
    """Make a price offer to the other party."""
    if price <= 0:
        raise ToolError("The price must be a positive number of dollars.")
    ctx.state.setdefault("offers", {})[ctx.agent_id] = price
    ctx.note("offer", price=price)
    return f"Your offer of ${price} is recorded."
```

The first parameter, if it is `ctx` or typed `ToolContext`, is filled in by the testbed and never shown to the model:

| `ctx.` | What it is |
|---|---|
| `agent_id` | The agent calling the tool |
| `files` | The workspace files: `list()`, `exists(name)`, `read(name)`, `write(name, text)`, `append(name, text)` (no newline added). Only the declared file if the tool has `file=`. |
| `state` | A dict shared by every tool call of every agent in this trial |
| `variables` | The trial's variable values |
| `rng` | A random generator seeded for this trial |
| `round`, `turn` | Where in the trial the call happened |
| `note(message, **data)` | Writes a `tool_note` event to the trial log (agents never see it) |
| `stop_trial(reason)` | Ends the trial after this call; `reason` becomes the stop reason |
| `finish_agent()` | This agent takes no more turns |

Behavior:
- **Return value.** Return a string, or anything JSON can encode (it is sent as JSON).
- **Invalid arguments.** Arguments that don't match the type hints are refused with "Invalid arguments: …"; your function doesn't run.
- **Errors.** Two kinds of error go back to the model as exactly their message: `raise ToolError("message")`, and file errors from `ctx.files` (for example "No file named 'x.txt'"). Any other exception is sent as `ErrorType: message`. Either way the trial continues; the error is part of the agent's experience, and it is logged as `tool_failed`.
- **Sync or async.** Both work. Sync functions run in a thread, so a slow tool doesn't hold up other trials. Both kinds are stopped after `limits.tool_timeout_seconds`.
- **Truncated replies.** If the model's reply was cut off by its output limit, its tool calls are not run (`tool_skipped`), and the model is told so.
- **Files.** Files may only be named, never given a path. `limits.max_file_bytes` and `limits.max_tool_input_bytes` cap sizes.
- **Frozen code.** Code runs on your machine with your permissions. When a batch starts, the tool files it uses are copied into the batch folder, so results always match the code that produced them.

### The code check

The Tools page checks code without running it, then imports it in a separate process, so a broken file can't crash the UI. Each problem comes with its line.

| | Errors (block Save) | Warnings |
|---|---|---|
| **Python** | syntax errors; errors while importing the file | from [pyflakes](https://pypi.org/project/pyflakes/): undefined names, unused imports and variables, redefinitions |
| **Tool rules** | not exactly one `@tool` in the file; a function name that differs from the file name; an argument without a type hint; `*args`/`**kwargs`; a `file=` that isn't a plain file name; an `op=` that isn't read, write, append or list | no docstring (the model gets no description); no return type |

**Files with several tools.** A file with several `@tool` functions, as older versions made, still runs: agents refer to its tools as `file:function`, for example `pair:ping`. The Tools page marks such a file ✗, because it edits one tool per file. To edit those tools there, copy each into its own tool.

## Writing a check

A check scores a finished trial. It is a function in `checks/<file>.py`, marked with `@check`. Scenarios list it as `file:function` in `checks`. Add checks on the UI's Checks page, or put the file in `checks/` yourself.

```python
from testbed import TrialRecord, check


@check
def output_matches_token(trial: TrialRecord) -> bool:
    """output.txt equals the token exactly."""
    return trial.files.get("output.txt") == trial.variables["token"]
```

`TrialRecord` gives you:

| | |
|---|---|
| `trial.files` | final workspace contents, name → text |
| `trial.variables` | the trial's variable values |
| `trial.tool_calls(agent=None, tool=None, file=None, op=None)` | tool calls in order: `{agent, tool, arguments, file, op, round, seq, ok, result}`. `file` and `op` (read, write, append, list) are filled in for tools that declare them with `@tool(file=..., op=...)`, as all predefined tools do, so "did A append to log.txt?" is `trial.tool_calls(agent="A", file="log.txt", op="append")`, whatever the tool is called. |
| `trial.messages(agent=None)` | each agent's message per turn: `{agent, round, text}` |
| `trial.events` / `trial.events_of(type)` | the raw event log (see [results](results.md)) |
| `trial.manifest` | everything the agents were given |
| `trial.status` | the trial's status |

Return a bool, number, string, `None` or anything else JSON can encode.

**When checks run:** after every trial with status `completed` or `timeout`. Trials that failed for infrastructure reasons are marked n/a and never counted.

**If a check raises:** its value is `None` and the error is saved in `check_errors`; the other checks still run.

**Re-running checks** uses the saved trials and makes no API calls:
- **UI:** Results → Re-run checks. Choose the batch's saved copy of the checks, or your current project checks; the old copy is kept.
- **CLI:**

  ```bash
  python -m testbed recheck runs/<batch>
  ```

## Providers and parameters

`params` are passed to the provider as-is, except two keys:
- `max_tokens` works for every provider; OpenAI and Gemini receive it as `max_output_tokens`.
- `cache` is the testbed's own key (default true), which turns prompt caching on or off.

| Provider | Notes |
|---|---|
| Anthropic | Messages API. Default `max_tokens` 16000. With caching on, the request carries `cache_control` so the growing history is cached. Recent models reject `temperature`; leave it out unless the model accepts it. |
| OpenAI | Responses API, stateless (`store: false`), so every logged request holds the full history; reasoning items are replayed encrypted. Caching is automatic. |
| Gemini | `google-genai`, with the SDK's automatic function calling off (the testbed runs tools). Caching is implicit. Also reads `GOOGLE_API_KEY`. |

How tool errors are reported back differs by provider API:
- **Anthropic:** `is_error: true` on the tool result.
- **Gemini:** an `error` field instead of `result`.
- **OpenAI:** the Responses API has no error flag, so only the message text tells the model.

New agents get each provider's cheap default model (`testbed/providers/models.yaml`, where you can also list more models). The model field also accepts any model id as text.
