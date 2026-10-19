"""Private JSONL event log for one trial. Adapted from v1/logger.py.

No tool can reach this file: it sits in the trial folder, outside workspace/.
"""

import gzip
import json
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path


class EventLogger:
    def __init__(self, path: Path, run_id: str):
        self.path = path
        self.run_id = run_id
        self.sequence = 0
        self.stream = path.open("x", encoding="utf-8")
        # Sync tools run in worker threads and may call ctx.note().
        self.lock = threading.Lock()

    def emit(self, event_type: str, **data) -> None:
        with self.lock:
            self.sequence += 1
            record = {
                "run_id": self.run_id,
                "seq": self.sequence,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event_type": event_type,
                "data": data,
            }
            self.stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            self.stream.flush()

    def close(self) -> None:
        with self.lock:
            if not self.stream.closed:
                self.stream.close()


def read_events(trial_dir: Path) -> list[dict]:
    """Events of one trial, from events.jsonl or events.jsonl.gz."""
    plain = trial_dir / "events.jsonl"
    packed = trial_dir / "events.jsonl.gz"
    if plain.exists():
        lines = plain.read_text(encoding="utf-8").splitlines()
    elif packed.exists():
        with gzip.open(packed, "rt", encoding="utf-8") as stream:
            lines = stream.read().splitlines()
    else:
        return []
    events = []
    for line in lines:
        if line.strip():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                break  # a trial killed mid-write can leave a partial last line
    return events


def compress_events(trial_dir: Path) -> None:
    plain = trial_dir / "events.jsonl"
    if not plain.exists():
        return
    with plain.open("rb") as source, gzip.open(trial_dir / "events.jsonl.gz", "wb") as target:
        shutil.copyfileobj(source, target)
    plain.unlink()
