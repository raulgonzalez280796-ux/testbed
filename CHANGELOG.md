# Changelog

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
