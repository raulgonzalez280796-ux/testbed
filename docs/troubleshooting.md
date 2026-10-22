# Troubleshooting

## Keys

**"This scenario needs ANTHROPIC_API_KEY" (or OPENAI_API_KEY, GEMINI_API_KEY).** A run needs a key only for the providers its agents use. Add it on the Settings page, or in `.env` in the project folder.

**A key is set but runs fail with `auth`.** On the Settings page, click **Test key**: it makes one small request and shows the provider's answer. An environment variable wins over `.env`, so an old key exported in your shell hides the one in `.env`. Settings shows "from environment" in that case.

**Gemini.** `GOOGLE_API_KEY` is accepted as well as `GEMINI_API_KEY`.

## Rate limits, failed trials and stopped batches

**Many `rate_limited` events, or slow progress.** The provider is answering 429 (too many requests). Each call waits as long as the provider asks and is retried, and the testbed sends fewer requests at once until the errors stop. Set your account's real limits on the Settings page, or in `settings.yaml`. Running more trials in parallel does not help past those limits: the extra trials just wait.

**Trials end as `infra_error`.** A model call kept failing after all retries. `result.json` → `error`, and the `model_call_failed` event, show the kind (`rate_limit`, `auth`, `bad_request`, `not_found`, `server`, `connection`, `timeout`), the HTTP status and the provider's message. `bad_request` usually means a parameter the model doesn't accept: check the agent's `params` (for example, `temperature` on models that reject it), or a misspelled model id (`not_found`).

**The batch stopped with "Stopped after 5 trials in a row failed with the same error".** That's the circuit breaker: when the same error keeps repeating (a bad key, an exhausted quota), the batch stops instead of failing every trial. Fix the cause, then resume.

**The batch stopped with "Token cap reached".** The batch reached `budget.max_total_tokens`. Raise the cap in the scenario's Checks & budget tab if you want more trials, then start a new batch. (The cap is frozen into the batch.)

**Resume a stopped or interrupted batch.** Trials that completed are kept; the others run again with the same seeds:
- **UI:** the Run page → Resume.
- **CLI:**

  ```bash
  python -m testbed resume runs/<batch_id>
  ```

**A batch shows "running" but nothing happens.** The runner process stopped (for example, the computer restarted). The Run page says so, and offers Resume.

**Cancel a batch.** Use Cancel on the Run page, or:

```bash
python -m testbed cancel runs/<batch_id>
```

## Scenarios

**"agent X has no turn-1 input".** In environment mode, every agent needs a `shared_task` or its own `task_prompt`.

**"… has N turns but no continuation_prompt (required in environment mode)".** Add a continuation prompt, or switch to conversation mode, where the other agents' messages can be the input.

**"… uses {{name}}, which is not a defined variable".** Add the variable on the Variables tab, or fix the spelling.

**"schedule.order leaves out …".** A hand-written scenario's `schedule.order` must list every agent. The UI editor adds missing agents at the end by itself.

**"no tool called 'read_log': there is no file tools/read_log.py".** An agent lists a tool that isn't in the project's `tools/` folder. Add it on the Tools page, or take it off the agent. The predefined tools (`check_env`, `read_log`, `write_log`, …) come with `python -m testbed init <project>`, which only adds files that are missing, so it is safe to run on an existing project.

**A tool is marked ✗ on the Tools page, or a check is "missing".** Open the tool: its problems are listed with line numbers. A check is named `file:function` and lives in `checks/`; the Checks page shows **ok** or the error for each file.

**"'access' was removed" or "the scenario's 'tools:' section was removed".** The scenario was written for an older version, where files had an access grid and a scenario could declare its own tools. Now every tool is a file in `tools/`, and the file it works on is part of the tool. Open the scenario in the UI: the editor removes the old parts, explains what changed, and you save. Then check each agent's tools on the Agents tab. In a hand-written file, delete `access:` under each file and the top-level `tools:` section. Agents that used `read_file`, `write_file` or `append_file` need tools that each work on one file, such as `read_log` or `write_output`.

**Saving from the UI removed my comments.** Saving writes a standard YAML layout. The first time a file with comments is saved from the UI, the original is kept next to it as `<name>.yaml.bak`.

## Tools and checks

**A tool's error reaches the model, not you.** That's intended: a failing tool is part of what the agent experiences. The error is logged as `tool_failed`; in Results, it shows in red in the trial's timeline.

**A check shows `None`.** Either the check returned `None`, or it raised an error. Raised errors appear in the trial's Checks tab and in `result.json` → `check_errors`. Fix the check, then use Results → Re-run checks → "With the current checks from your project".

## The UI

**The code editor doesn't respond, or looks broken.** Turn on **Plain text code editor** in the sidebar to use a simple text box.

**"Port 8501 is not available".** Another UI, perhaps your own from earlier, is already running. Use it, stop it with Ctrl+C in its terminal, or start this one on another port:

```bash
python -m testbed ui --port 8502
```

**The first page takes a while.** About 10 seconds after starting the UI is normal: the provider SDKs load once. Later pages are fast.

**A blank page or an error after updating the testbed.** Stop the UI with Ctrl+C and start it again.

**Using the UI from another computer.** It listens on this computer only (127.0.0.1) and has no login. On a server, reach it through an SSH tunnel, never by opening the port.
