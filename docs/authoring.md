# Writing scenarios, tools and checks

A **scenario** is one YAML file in your project's `scenarios/` folder. The UI's Scenarios page edits the same file; the CLI and the Python API run it. Every field is listed in the [scenario reference](scenario-reference.md). This guide explains how the pieces behave together.

The four templates (`python -m testbed init` copies them into a project) are worked examples:

| Template | Shows |
|---|---|
| `help_seeking` | environment mode, single-purpose tools (each tool = one action on one file), no code |
| `help_seeking_v1` | researcher tools that reproduce another tool set exactly (`tools/v1_tools.py`) |
| `secret_keeper` | conversation mode, a researcher tool that ends the trial, a hidden sender (`conversation_format: "{text}"`) |
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

## Tools: three kinds

An agent's `tools` list can mix three kinds of tool. The model sees a name, a description and the arguments of each; nothing else.

| Kind | Example | Use it when |
|---|---|---|
| **Single-purpose tools** (`tools:` in the scenario) | `read_log`, `write_log` | Each tool should do one thing to one file, as in v1. No code. |
| **Generic file tools** (built in) | `read_file(name)`, `write_file(name, content)` | One general tool should work on several files, with permissions per agent in the access grid |
| **Researcher tools** (your Python code) | `negotiation:make_offer` | The tool needs logic: rules, scores, shared state, ending the trial |

### Single-purpose tools

Declare them once in the scenario's `tools:` section (the UI's **Tools** tab, or the "+ read tool / + write tool / + append tool" buttons under each file on the **Files** tab), then give them to agents by name:

```yaml
tools:
  - {name: check_env,    kind: list_files}                  # every file name in the workspace
  - {name: read_log,     kind: read_file,   file: log.txt}
  - {name: write_log,    kind: append_file, file: log.txt}   # adds a line; history is kept
  - {name: write_output, kind: write_file,  file: output.txt}
agents:
  - id: A
    tools: [check_env, read_log, write_log]
```

| Kind | Arguments the model fills in | Does |
|---|---|---|
| `read_file` | none | Returns the file's content |
| `write_file` | `content` | Replaces the file's content |
| `append_file` | `text` | Adds the text and a newline at the end |
| `list_files` | none | Lists file names. `files: [...]` limits what it shows; without it, every file in the workspace is listed. |

None of them takes a file name: the file is fixed when you declare the tool, so a tool can never reach another file. **Giving the tool to an agent is the permission**; the access grid plays no part. `description` is optional. The default follows v1's wording, e.g. "Read the UTF-8 contents of log.txt.". Because the name and the description are part of what the agent experiences, both are yours to choose, and both are recorded in each trial's manifest.

A scenario tool with the same name as a built-in (for example `list_files`) replaces the built-in for that scenario.

### Generic file tools and the access grid

`list_files`, `read_file(name)`, `write_file(name, content)` and `append_file(name, text)` take a file name. What each agent may do is set per file in the **access table**: agent id (or `*` for everyone) → operations.

| Operation | Lets the agent |
|---|---|
| `list` | see the file's name in `list_files` (nothing else) |
| `read` | `read_file` |
| `write` | `write_file` (replace the content) |
| `append` | `append_file` (adds the text and a newline) |

Any operation also makes the file show up in the generic `list_files`. A refused call returns an error to the model, such as "You do not have permission to write 'log.txt'.", and the error is logged. A name the agent has no access to at all gets "No accessible file named …", whether or not the file exists.

Files may only be named, never given a path. `limits.max_file_bytes` and `limits.max_tool_input_bytes` cap sizes. Researcher tools can reach every file through `ctx.files`, regardless of the table; your tool decides.

## Writing a researcher tool

A researcher tool is a Python function in `tools/<file>.py`, marked with `@tool`. Scenarios refer to it as `file:function`, e.g. `negotiation:make_offer`. Add tools on the UI's Tools & checks page (paste or upload), or put the file in `tools/` yourself.

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

What the model sees:
- **Name:** the function name (or `@tool(name=...)`).
- **Description:** the docstring (or `@tool(description=...)`).
- **Arguments:** a JSON schema built from the type hints. `Annotated[type, "text"]` adds a description to an argument; defaults make an argument optional.
- **Schema override:** `@tool(schema={...})` replaces the generated schema exactly, while arguments are still checked against the type hints. `tools/v1_tools.py` uses it to reproduce v1's schemas.

The first parameter, if it is `ctx` or typed `ToolContext`, is filled in by the testbed and never shown to the model:

| `ctx.` | What it is |
|---|---|
| `agent_id` | The agent calling the tool |
| `files` | Every workspace file: `list()`, `exists(name)`, `read(name)`, `write(name, text)`, `append(name, text)` (no newline added) |
| `file_access` | The calling agent's access table (file → operations) |
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
- **Errors.** `raise ToolError("message")` sends exactly that message back as an error. Any other exception is sent as `ErrorType: message`. Either way the trial continues; the error is part of the agent's experience, and it is logged as `tool_failed`.
- **Sync or async.** Both work. Sync functions run in a thread, so a slow tool doesn't hold up other trials. Both kinds are stopped after `limits.tool_timeout_seconds`.
- **Truncated replies.** If the model's reply was cut off by its output limit, its tool calls are not run (`tool_skipped`), and the model is told so.
- **Frozen code.** Code runs on your machine with your permissions. When a batch starts, the tool files it uses are copied into the batch folder, so results always match the code that produced them.

## Writing a check

A check scores a finished trial. It is a function in `checks/<file>.py`, marked with `@check`. Scenarios list it as `file:function` in `checks`.

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
| `trial.tool_calls(agent=None, tool=None, file=None, op=None)` | tool calls in order: `{agent, tool, arguments, file, op, round, seq, ok, result}`. `file` and `op` (read, write, append, list) are filled in for single-purpose and generic file tools, so "did A append to log.txt?" is `trial.tool_calls(agent="A", file="log.txt", op="append")`, whatever the tool is called. |
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
