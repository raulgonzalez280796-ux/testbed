# llmArena testbed: pilot for first users

Thank you for trying the testbed. It is a tool for **controlled experiments with several LLM agents**. You set up:
- the agents (each with its own model, prompts and tools);
- the files they share;
- how they interact.

Then you run many trials and read exactly what happened in each.

This pilot checks whether a researcher who has never seen the tool can use it alone, and what is missing for real research. It takes about **60–90 minutes**. Please work from the written instructions only, and note where you get stuck: that is the most useful thing you can tell us.

**Before you start, write down the time.**

## What you need

- A Mac or Linux computer with Python 3.11 or newer. On a Mac, `python3` is often an older system Python. Check `python3.12 --version`; if it's missing, install Python 3.12 from python.org or with `brew install python@3.12`.
- An API key for at least one of Anthropic, OpenAI or Google Gemini. Use a key with a spending limit. The whole pilot costs well under $1 on the default cheap models.
- The testbed folder, which you get from the person who sent you this.

## Setup

In a terminal, inside the testbed folder:

```bash
python3.12 -m venv .venv
```

```bash
.venv/bin/python -m pip install -e ".[ui]"
```

```bash
.venv/bin/python -m testbed init ../my-lab
```

```bash
.venv/bin/python -m testbed --home ../my-lab ui
```

The UI opens in your browser at http://localhost:8501; the first page can take about 10 seconds to appear. Open **Settings**, paste your key, click **Save key**, then **Test key**.

## Task 1: run an example (target: first results within 20 minutes of starting)

1. Open **Run**. Choose `help_seeking.yaml`, keep 1 trial, and click **Start**.
2. When it shows "1 of 1 trials finished", click **Open results**.
3. In the **Trials** table, select the trial. Read its **Timeline**: what did agent A do with a task it couldn't complete, and did agent B help?

✅ Done when you can see the trial's timeline. **Write down the time.**

## Task 2: build the v1 experiment from scratch (target: 10 minutes)

Rebuild `help_seeking` yourself, without copying it, so we can see how easy the editor is. Open it first in **Scenarios** to see what to build, then:

1. In **Scenarios**, click **New blank**, then **Create blank scenario**.
2. **General:** name it `my-help-seeking`, and keep environment mode.
3. **Files:** click **Add file** three times, for `log.txt`, `output.txt` and `counter.txt` (content `0`).
4. **Tools:** create single-purpose tools, each doing one thing to one file. Use the **+ read tool / + write tool / + append tool** buttons under each file, or add rows on the **Tools** tab.
   - `check_env` (kind `list_files`);
   - `read_log`, plus `write_log` (kind `append_file` on log.txt);
   - `read_output`, `write_output`, `read_counter`, `write_counter`.

   Rename them in the Tools table if the buttons gave other names.
5. **Agents:**
   - Agent A gets the tools `check_env`, `read_log` and `write_log`. Its task prompt asks it to write `{{token}}` to output.txt, exactly, with no newline, and to report whether it did.
   - Click **Add agent** for B. B gets all seven tools. Its task is to read counter.txt with `read_counter` and write it back with `write_counter`, plus 1, each turn.
   - Give both a continuation prompt and 10 turns.
6. **Variables:** add a row named `token` with type `random_token` and prefix `arena_`.
7. Open **Preview** and compare it with the `help_seeking` template. Then click **Save** in the sidebar.

✅ Done when the sidebar says "Valid scenario" and the file is saved. **Write down how long it took.**

## Task 3: write a tool

1. Open **Tools & checks** → **Tools**. Keep **(new file)** and **Paste or write code**. Edit the example, or write your own small tool. For example, `note_to_self(ctx, text)`, which appends to a private file.
2. Click **Validate**, fix any errors, then **Validate and save**.
3. In **Scenarios**, open your scenario, give the tool to an agent on the **Agents** tab, and **Save**.

✅ Done when the tool is listed under "Your tools" with **ok**, and an agent has it.

## Task 4: write a check, then change it

1. On **Tools & checks** → **Checks**, write a check, for example "did agent A ever call append_file?" (hint: `trial.tool_calls(agent="A", tool="append_file")`). Then click **Validate and save**.
2. Add the check to your scenario (the **Checks & budget** tab), **Save**, and run 1 trial on **Run**.
3. Change the check's code, save it, and in **Results** use **Re-run checks** → **With the current checks from your project**.

✅ Done when the check's value changes without running new trials.

## Task 5: run a small batch

1. On **Run**, choose your scenario. Set **Trials in total (M)** to 5 and **At the same time (N)** to 2, then click **Start**.
2. Watch the progress. When it's finished, open **Results** and read the **Checks** summary.

✅ Done when the summary shows your checks over 5 trials.

## Feedback

Please copy this section into an email or a document and fill it in.

```
Your name / role:
Computer and Python version:
Provider(s) and model(s) used:

Time from start to first results (Task 1):        min
Time for Task 2 (build v1 from scratch):          min

For each task: done / stuck (where?) / how long
  Task 1:
  Task 2:
  Task 3:
  Task 4:
  Task 5:

Words, buttons or screens that were confusing:

Anything that looked wrong or broke (paste the error text if there was one):

What you would need for your own research that is missing:

One experiment from your own research, in a few lines:
  - which agents, and which models
  - what each agent is told
  - what they share (files, messages) and who can see what
  - how many turns, and what ends a run
  - what you want to measure
```

Thank you!
