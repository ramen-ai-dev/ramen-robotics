"""Hash-chained JSONL journal of every workcell decision (holds and releases).

Each entry carries ``prev_sha256`` (the previous entry's ``entry_sha256``) and
``entry_sha256`` (SHA-256 of the entry's canonical JSON without that field). Editing,
removing or reordering a line breaks the chain. Lines are written through
:class:`ramen_robotics.gate.AuditTrail` (owner-only file mode, fsync per line).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ramen_robotics.gate import AuditTrail

from .profile import canonical_json

JOURNAL_SCHEMA = "ramen-robotics.workcell/1"
GENESIS = "0" * 64


def entry_digest(entry: Mapping[str, Any]) -> str:
    body = {key: value for key, value in entry.items() if key != "entry_sha256"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


class WorkcellJournal:
    """Append decisions to ``path``, resuming the chain if the file already has entries."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._trail = AuditTrail(self.path)
        self._lock = threading.Lock()
        self._head, self._seq = GENESIS, 0
        if self.path.is_file():
            last = None
            for entry in read_entries(self.path):
                last = entry
            if last is not None:
                self._head = str(last["entry_sha256"])
                self._seq = int(last["seq"])

    @property
    def head(self) -> str:
        return self._head

    def next_seq(self) -> int:
        with self._lock:
            return self._seq + 1

    def append(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Add chain fields to ``record`` (JSON-native values only) and write it."""
        with self._lock:
            entry: dict[str, Any] = {
                **record,
                "schema": JOURNAL_SCHEMA,
                "seq": self._seq + 1,
                "prev_sha256": self._head,
            }
            canonical_json(entry)  # rejects NaN and non-JSON values before anything is written
            entry["entry_sha256"] = entry_digest(entry)
            self._trail.append(entry)
            self._head, self._seq = entry["entry_sha256"], entry["seq"]
            return entry


def read_entries(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def verify_chain(entries: Iterable[Mapping[str, Any]], *, anchor: str = GENESIS) -> list[str]:
    """Return chain problems; an empty list means every entry links and hashes correctly."""
    problems: list[str] = []
    previous = anchor
    expected_seq: int | None = None
    for index, entry in enumerate(entries, start=1):
        if entry.get("schema") != JOURNAL_SCHEMA:
            problems.append(f"entry {index}: schema is not {JOURNAL_SCHEMA}")
        if entry.get("prev_sha256") != previous:
            problems.append(f"entry {index}: prev_sha256 does not link to the previous entry")
        if entry.get("entry_sha256") != entry_digest(entry):
            problems.append(f"entry {index}: entry_sha256 does not match its contents")
        seq = entry.get("seq")
        if expected_seq is not None and seq != expected_seq:
            problems.append(f"entry {index}: seq {seq} is out of order")
        expected_seq = (seq + 1) if isinstance(seq, int) else None
        previous = str(entry.get("entry_sha256"))
    return problems
