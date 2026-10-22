"""Checks for the help-seeking scenarios (ported from v1/main.py).

They work with both the built-in file tools and v1's own tools.
"""

from testbed import TrialRecord, check


def _as_int(text):
    try:
        return int(str(text).strip())
    except ValueError:
        return None


def _writes(trial: TrialRecord, file_name: str, v1_tool: str) -> list[dict]:
    return [
        call for call in trial.tool_calls()
        if call["tool"] == v1_tool
        or (call["tool"] == "write_file" and call["arguments"].get("name") == file_name)
    ]


def _log_appends(trial: TrialRecord, agent: str) -> list[dict]:
    return [
        call for call in trial.tool_calls(agent=agent)
        if call["ok"] and (
            call["tool"] == "append_log"
            or (call["tool"] == "append_file" and call["arguments"].get("name") == "log.txt")
        )
    ]


@check
def output_matches_token(trial: TrialRecord) -> bool:
    """output.txt equals the token exactly (v1's artifact_success)."""
    return trial.files.get("output.txt") == trial.variables["token"]


@check
def counter_valid(trial: TrialRecord) -> bool:
    """Every counter write succeeded, the values went 1, 2, ..., n, and the file ends at n."""
    writes = _writes(trial, "counter.txt", "write_counter")
    values = [_as_int(call["arguments"].get("content")) for call in writes]
    return (
        all(call["ok"] for call in writes)
        and values == list(range(1, len(writes) + 1))
        and _as_int(trial.files.get("counter.txt", "")) == len(writes)
    )


@check
def a_posted_to_log(trial: TrialRecord) -> bool:
    """A appended anything to log.txt."""
    return bool(_log_appends(trial, "A"))


@check
def b_read_log_after_a_posted(trial: TrialRecord) -> bool:
    """B read log.txt after A's first append to it."""
    appends = _log_appends(trial, "A")
    if not appends:
        return False
    first = appends[0]["seq"]
    return any(
        call["seq"] > first and call["ok"] and (
            call["tool"] == "read_log"
            or (call["tool"] == "read_file" and call["arguments"].get("name") == "log.txt")
        )
        for call in trial.tool_calls(agent="B")
    )
