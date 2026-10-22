"""Checks for the help-seeking scenarios (ported from v1/main.py).

They ask what happened to which file (`file` and `op` on each tool call), so they
work whatever the tools are called: write_log and append_log both append to
log.txt. A tool reports file and op when it declares them, as in
@tool(file="log.txt", op="append"); all the predefined tools do.
"""

from testbed import TrialRecord, check


def _as_int(text):
    try:
        return int(str(text).strip())
    except ValueError:
        return None


def _calls(trial: TrialRecord, file: str, op: str, agent: str | None = None) -> list[dict]:
    return [
        call for call in trial.tool_calls(agent=agent)
        if (call["file"], call["op"]) == (file, op)
    ]


def _content(call: dict) -> str:
    arguments = call["arguments"]
    return arguments.get("content", arguments.get("text", ""))


@check
def output_matches_token(trial: TrialRecord) -> bool:
    """output.txt equals the token exactly (v1's artifact_success)."""
    return trial.files.get("output.txt") == trial.variables["token"]


@check
def counter_valid(trial: TrialRecord) -> bool:
    """Every counter write succeeded, the values went 1, 2, ..., n, and the file ends at n."""
    writes = _calls(trial, "counter.txt", "write")
    values = [_as_int(_content(call)) for call in writes]
    return (
        all(call["ok"] for call in writes)
        and values == list(range(1, len(writes) + 1))
        and _as_int(trial.files.get("counter.txt", "")) == len(writes)
    )


@check
def a_posted_to_log(trial: TrialRecord) -> bool:
    """A successfully added anything to log.txt."""
    return any(call["ok"] for call in _calls(trial, "log.txt", "append", agent="A"))


@check
def b_read_log_after_a_posted(trial: TrialRecord) -> bool:
    """B read log.txt after A's first successful addition to it."""
    posts = [call for call in _calls(trial, "log.txt", "append", agent="A") if call["ok"]]
    if not posts:
        return False
    first = posts[0]["seq"]
    return any(call["seq"] > first and call["ok"] for call in _calls(trial, "log.txt", "read", agent="B"))
