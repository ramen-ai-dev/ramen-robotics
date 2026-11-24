"""Append-only JSONL audit trail and offline re-verification.

Each dispatch is one JSON line. Lines with ``"evaluated": true`` carry the exact
evaluated text and the Schema V5 receipt, so :func:`verify_audit_file` can check
them again later with nothing but the pinned public keys.

Trial receipts (``trial_<uuid>``) verify cryptographically, but they come from the
shared public trial and are not persistent audit proof. :func:`verify_audit_file`
reports them as problems unless ``allow_trial=True``.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .receipts import receipt_class, receipt_problem

AUDIT_SCHEMA = "ramen-robotics.audit/1"


class AuditTrail:
    """Append audit records to a JSONL file created with owner-only permissions."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, record: Mapping[str, Any]) -> None:
        line = json.dumps({"schema": AUDIT_SCHEMA, **record}, sort_keys=True, default=str) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())


def verify_audit_file(
    path: str | os.PathLike[str],
    *,
    allow_trial: bool = False,
    public_keys: Mapping[str, str] | None = None,
) -> list[str]:
    """Re-verify every evaluated line. Returns problems; an empty list means every line verified.

    Trial receipts are reported as problems unless ``allow_trial`` is true. The
    receipt class is derived from the receipt id, never from the recorded field.
    """
    problems: list[str] = []
    with open(path, encoding="utf-8") as handle:
        for number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                problems.append(f"line {number}: not valid JSON")
                continue
            if not isinstance(entry, dict):
                problems.append(f"line {number}: not a JSON object")
                continue
            if entry.get("evaluated") is not True:
                continue
            verdict = entry.get("verdict")
            input_text = entry.get("input_text")
            receipt = entry.get("receipt")
            if verdict not in ("ALLOW", "BLOCK") or not isinstance(input_text, str):
                problems.append(f"line {number}: missing verdict or input_text")
                continue
            if receipt is not None and not isinstance(receipt, dict):
                problems.append(f"line {number}: receipt is not an object")
                continue
            problem = receipt_problem(
                receipt, input_text, allowed=verdict == "ALLOW", public_keys=public_keys
            )
            if problem is not None:
                problems.append(f"line {number}: {problem}")
                continue
            if receipt_class(receipt) == "trial" and not allow_trial:
                problems.append(
                    f"line {number}: receipt {receipt.get('id') if receipt else None} is a trial receipt, "
                    "not persistent audit proof"
                )
    return problems
