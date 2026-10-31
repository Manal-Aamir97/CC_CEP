"""The study's event log (build plan Step 6): one JSON-lines file per participant and session.

    logs/P01_session0.jsonl   consent, background questionnaire, practice task
    logs/P01_session1.jsonl   part 1 (three tasks)
    logs/P01_session2.jsonl   part 2 (three tasks)
    logs/P01_session3.jsonl   post-questionnaire and session end

Every line is one event with a server timestamp ("at", UTC). Each line is flushed to disk as it is
written, so a crash loses at most the event being written. Files are only ever appended to.
"""
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

LOG_VERSION = 1
SAFE_ID = re.compile(r"P\d{2,3}")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class EventLog:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def path(self, participant_id, part):
        if not SAFE_ID.fullmatch(participant_id) or not isinstance(part, int) or not 0 <= part <= 9:
            raise ValueError(f"bad log name: {participant_id!r}, {part!r}")
        return self.folder / f"{participant_id}_session{part}.jsonl"

    def write(self, entry):
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        with self._lock, open(self.path(entry["participant_id"], entry["part"]), "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    def has_participant(self, participant_id):
        return any(self.folder.glob(f"{participant_id}_session*.jsonl"))


def read_events(folder):
    """All events in a log folder, in file then line order (used by the analysis)."""
    events = []
    for path in sorted(Path(folder).glob("P*_session*.jsonl")):
        with open(path, encoding="utf-8") as f:
            events.extend(json.loads(line) for line in f if line.strip())
    return events
