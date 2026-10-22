# Changelog

## Unreleased

**The trial script: every step of a trial, in order, on its own page.**
- **A trial page.** Each trial has its own page and link (`trial?batch=…&trial=N`).
  - Open it from **Open ↗** in the Results trials table, or from a running trial's button on the Run page. It opens in a new browser tab, so several trials can be open at once.
  - **← / →** go to the previous and next trial.
  - Results no longer shows the trial inline.
- **The Script tab** shows the trial in the log's order. Each step shows the time since the start, the agent, and what is new at that step:
  - what the agent received: in conversation mode, the other agents' messages apart from the instruction; on its first turn, its system prompt and tools;
  - what it wrote, verbatim, with tokens and duration;
  - each tool call, with its arguments, result or error, file and action;
  - retries, notes and turn ends.
- **Live.** While a trial runs, the script refreshes every 2 seconds, and a "now" line says what is happening (for example "B is waiting for the model (8 s)"). **Follow the newest steps** keeps the latest steps in view.
- **Filters and search** by agent, kind of step, tool, round and text. A **Raw events** switch shows the logged events under each step.
- **Downloads:** the whole script as `.md` or `.html`. The same from the CLI: `python -m testbed script <trial folder> [--format md|html] [--out FILE]`.
- `turn_started` events now also log `instruction`, and in conversation mode `messages` (each `{sender, round, text}`). `prompt` is unchanged.

**One tools library.** Every tool is now a Python file in `tools/`, and one page manages them all.
- A tool is one file holding one `@tool` function with the same name: `tools/read_log.py` is the tool `read_log`, and agents list tools by that name. `file:function` still works for older files that hold several tools.
- New `@tool(file="log.txt", op="append")`:
  - the tool can touch only that file, because `ctx.files` refuses any other name;
  - every call is logged with its `file` and `op` (read, write, append, list), so a check can ask what happened to a file whatever the tool is called (`TrialRecord.tool_calls(file=..., op=...)`).
- **Predefined tools**, one file each, which `init` copies into a project. They are ordinary tools you can edit, copy or delete.
  - File tools: `check_env`, `list_files`, `read_log`, `write_log`, `append_log`, `read_output`, `write_output`, `read_counter`, `write_counter`. They send the model v1's exact descriptions and schemas.
  - Scenario tools: `make_offer` and `accept_offer` (negotiation), `submit_secret` (secret_keeper).
  - The four templates use them by name. In `help_seeking`, A has `check_env`, `read_log` and `write_log`; B also has `read_output`, `write_output`, `read_counter` and `write_counter`.
- **A new Tools page:**
  - a list of every tool, with its check status, your note, what the model is told, the file it works on, and which scenarios use it;
  - **Add tool**, from a starter (read, write or append to a file, list the files, blank), pasted code or an uploaded `.py` file;
  - an editor with **Check** and **Save**;
  - **Your note**, kept in `tools/notes.yaml` and never sent to any model;
  - **Duplicate**, which renames the function too, and **Delete**, which first says which scenarios use the tool.
- **A code check** for tools. Each problem has a line number, and only errors block saving.
  - Errors: syntax and import errors, and the tool rules: one `@tool` per file, named like the file, type hints on every argument, a valid `file=` and `op=`.
  - Warnings: pyflakes findings (undefined names, unused imports), and a missing docstring or return type.
  - New dependency: `pyflakes`.
- Checks have their own **Checks** page (unchanged otherwise).
- The scenario editor no longer has a Tools tab or an access grid. The Agents tab picks from every tool in `tools/`, labeled with your notes, and the Preview shows which file each tool works on.
- Errors from `ctx.files` (for example "No file named 'x.txt'") reach the model as their plain message, like `ToolError`. Before, a researcher tool's file error was sent as `WorkspaceError: …`.

**Removed:**
- the generic file tools `read_file`, `write_file` and `append_file`, and the built-in `list_files` (now a predefined tool that lists every file, as in v1);
- the per-file access grid (`files[].access`) and `ctx.file_access`;
- the scenario `tools:` section (single-purpose tools declared in YAML), which was added after 0.1.0 and never released;
- the multi-tool template files `tools/v1_tools.py`, `negotiation.py` and `secret_keeper.py`, split into one file per tool.

A scenario that still uses `access` or `tools:` gets a validation message saying what to do. The UI editor removes them when it opens the file and explains the change. To get the predefined tools in an existing project, run `python -m testbed init <project>`: it only adds missing files.

## 0.1.0 (2026-10-22): first version

The first complete version of the llmArena testbed: controlled experiments with several LLM agents, set up in one YAML file or in a local web UI.

**Scenarios**
- Any number of agents, each with its own provider, model, prompts (system, turn 1, later turns), turns, model calls per turn and tools.
- Two interaction modes. In **environment** mode, agents act only through files and tools. In **conversation** mode, each turn also delivers the other agents' new messages, in a configurable format.
- Shared workspace files with per-agent access (list, read, write, append). Seeded per-trial variables (`random_token`, `constant`, `int_range`, `choice`).
- A fixed or shuffled turn order; stop rules for rounds, a timeout, token caps, and stops requested by tools.
- [Scenario reference](docs/scenario-reference.md) and JSON Schema, both generated from the code (`python -m testbed reference`, `python -m testbed schema`).

**Providers**
- Anthropic (Messages API, with prompt caching), OpenAI (Responses API, stateless) and Google Gemini (`google-genai`), behind one small adapter each. Provider-native content such as reasoning is replayed unchanged.
- Each request is logged exactly as passed to the provider's SDK, together with the raw response.
- Only Anthropic has been tested live so far. The OpenAI and Gemini adapters are tested offline, through their SDKs against recorded responses.

**Tools and checks**
- Built-in file tools, plus researcher tools written as `@tool` functions. A tool gets an injected `ctx` (files, shared state, variables, a seeded random generator, notes, and stopping the trial or the agent).
- `@check` functions score each finished trial. They can be re-run later on saved trials, with no API calls.

**Runs**
- One trial, or M trials with N at a time. Trials run as async tasks: 1000 trials at once fit in under 100 MB with a fake provider.
- Retries with backoff, and a per-provider rate limiter that adapts to 429s.
- A circuit breaker for repeated identical errors, resume with the same seeds, cancel.
- Token totals per trial and per batch, caps, and a pilot estimate with confirmation for batches above 20 trials.
- Each trial writes `manifest.json`, `events.jsonl.gz`, `result.json` and its final files ([details](docs/results.md)). Infrastructure failures get their own status and are never counted as results.

**Interfaces**
- A local web UI (`python -m testbed ui`) with five pages:
  - **Settings:** API keys, with a test button; rate limits.
  - **Scenarios:** an editor with a preview.
  - **Tools & checks:** paste code or upload a `.py` file, then validate it.
  - **Run:** live progress, cancel, resume.
  - **Results:** check summaries and a per-trial timeline.
- A CLI (`run`, `resume`, `cancel`, `status`, `recheck`, `validate`, `preview`, `keys`, `init`, `ui`, `schema`, `reference`), and a Python API (`run_batch`) usable from notebooks.
- `python -m testbed init` creates a project folder with four example templates: `help_seeking`, `help_seeking_v1` (an exact port of the v1 experiment), `secret_keeper` and `negotiation`.

**Fixed before release**
- Scenario editor:
  - Duplicating an agent could leave it out of the turn order and block saving. The order shown is now always the order saved, and missing agents go last.
  - Renaming an agent to an id that's already taken is refused, instead of breaking the page.
  - New agent ids after Z are A1, A2, ….
  - New: Discard changes, Save as copy, and a `.bak` of a hand-written file before its first save.
- Opening or refreshing a page URL directly (e.g. `/results`) no longer shows a blank page.
- The Run page shows batches whose runner stopped, with Resume.
