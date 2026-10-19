"""Executors run a frozen batch folder's trials somewhere.

Stage 1 ships the local executor. Later targets (Docker, remote servers,
cluster shards) run the same batch folder and trial indices and write the same
trial folders, so the engine and results format never change.
"""

from .local import MissingKeys, run_batch_dir

__all__ = ["MissingKeys", "run_batch_dir"]
