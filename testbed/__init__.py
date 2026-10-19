"""llmArena testbed: controlled experiments with multiple LLM agents."""

from ._version import __version__
from .checks import TrialRecord, check
from .pilot import ConfirmationRequired
from .tools.decorator import ToolContext, ToolError, tool


def run_batch(scenario, **options):
    from .api import run_batch as _run_batch
    return _run_batch(scenario, **options)


def run_pilot(scenario, **options):
    from .api import run_pilot as _run_pilot
    return _run_pilot(scenario, **options)


def resume_batch(batch_dir, **options):
    from .api import resume_batch as _resume_batch
    return _resume_batch(batch_dir, **options)


__all__ = [
    "__version__", "TrialRecord", "check", "ToolContext", "ToolError", "tool",
    "run_batch", "run_pilot", "resume_batch", "ConfirmationRequired",
]
