"""WorkcellGate: deterministic workcell pre-checks in front of :class:`RoboticsGate`.

Order for every request:

1. take one fresh :class:`CellSnapshot` from the :class:`StateProvider`;
2. run every applicable pre-check; any finding holds the request locally
   (``Outcome.LOCAL_BLOCK``, no evaluation request);
3. map the request to one industrial tool call and dispatch it through
   :class:`RoboticsGate`, which releases the host tool only on a verified ALLOW;
4. journal the decision, hold or release, in the hash-chained workcell journal.

The gate passed in must be built with ``audit_path`` set to ``receipts_path``: the
dossier exporter joins journal entries to those receipt lines by ``tool_call_id``.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from ramen_robotics.gate import GateDecision, Outcome, RoboticsGate

from .journal import WorkcellJournal
from .prechecks import Finding, FindingCode, Precheck, default_prechecks, run_prechecks
from .profile import CellProfile, json_safe
from .requests import RecipeExport, WorkcellRequest, request_kind, request_summary, to_dispatch
from .state import CellSnapshot, StateProvider

Stage = Literal["precheck", "gate", "local_only"]
LOCAL_ONLY_REASON = (
    "recipe export cleared the local egress checks; live egress evaluation is not part of this "
    "version, so the export is not released by the gate"
)
_TOOL_FOR = {
    "BatchStart": "dispatch_manipulation",
    "CellMotion": "dispatch_manipulation",
    "SetpointChange": "adjust_plc_setpoint",
    "SafetyParameterChange": "mutate_safety_parameter",
}


@dataclass(frozen=True)
class WorkcellDecision:
    """The result of :meth:`WorkcellGate.submit`. Only ``Outcome.RELEASED`` reached a host tool."""

    request_kind: str
    tool_call_id: str
    stage: Stage
    outcome: Outcome
    findings: tuple[Finding, ...]
    gate: GateDecision | None
    snapshot_digest: str | None
    config_hash: str
    rush: bool
    reason: str | None = None

    @property
    def hold(self) -> bool:
        return self.outcome is not Outcome.RELEASED

    @property
    def codes(self) -> tuple[FindingCode, ...]:
        return tuple(finding.code for finding in self.findings)


class WorkcellGate:
    """Govern supervisory requests for one cell. Thread-safe; one request at a time."""

    def __init__(
        self,
        profile: CellProfile,
        gate: RoboticsGate,
        state: StateProvider,
        *,
        journal_path: str | os.PathLike[str],
        receipts_path: str | os.PathLike[str],
        prechecks: Sequence[Precheck] | None = None,
        max_snapshot_age_s: float = 1.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not (max_snapshot_age_s > 0):
            raise ValueError("max_snapshot_age_s must be > 0")
        self.profile = profile
        self.gate = gate
        self.state = state
        self.receipts_path = Path(receipts_path)
        self.journal = WorkcellJournal(journal_path)
        self._prechecks = tuple(prechecks) if prechecks is not None else default_prechecks()
        self._max_age = max_snapshot_age_s
        self._clock = clock
        self._config_hash = profile.config_hash()
        self._lock = threading.Lock()

    def submit(self, request: WorkcellRequest) -> WorkcellDecision:
        """Check, then dispatch or hold, then journal one request."""
        with self._lock:
            call_id = f"{self.profile.cell_id}:{self.journal.next_seq():06d}:{uuid.uuid4().hex[:12]}"
            snapshot, findings = self._snapshot()
            if snapshot is not None:
                findings += run_prechecks(self._prechecks, request, snapshot, self.profile)
            if isinstance(request, RecipeExport):
                if findings:
                    return self._finish(
                        request, call_id, "precheck", Outcome.LOCAL_BLOCK, findings, None, snapshot
                    )
                return self._finish(
                    request,
                    call_id,
                    "local_only",
                    Outcome.HOLD_UNAVAILABLE,
                    (),
                    None,
                    snapshot,
                    LOCAL_ONLY_REASON,
                )
            tool = _TOOL_FOR[request_kind(request)]
            if tool not in self.gate.tool_names:
                findings += (Finding(FindingCode.TOOL_NOT_REGISTERED, "gate", f"{tool} is not registered"),)
            if findings or snapshot is None:
                return self._finish(
                    request, call_id, "precheck", Outcome.LOCAL_BLOCK, findings, None, snapshot
                )
            try:
                tool_name, payload = to_dispatch(request, snapshot, self.profile)
            except Exception as error:  # mapping failure is a hold
                failure = (Finding(FindingCode.PRECHECK_ERROR, "to_dispatch", type(error).__name__),)
                return self._finish(
                    request, call_id, "precheck", Outcome.LOCAL_BLOCK, failure, None, snapshot
                )
            decision = self.gate.dispatch(tool_name, payload, tool_call_id=call_id)
            return self._finish(
                request, call_id, "gate", decision.outcome, (), decision, snapshot, decision.reason
            )

    def check_recipe_export(self, request: RecipeExport) -> WorkcellDecision:
        """Alias of :meth:`submit` for :class:`RecipeExport`; never returns ``RELEASED``."""
        return self.submit(request)

    def _snapshot(self) -> tuple[CellSnapshot | None, tuple[Finding, ...]]:
        try:
            snapshot = self.state.snapshot()
        except Exception as error:  # an unreadable cell is a hold
            return None, (Finding(FindingCode.PRECHECK_ERROR, "state_provider", type(error).__name__),)
        age = self._clock() - snapshot.taken_at
        if not (abs(age) <= self._max_age):  # also catches NaN; small clock skew either way is tolerated
            return snapshot, (
                Finding(
                    FindingCode.SNAPSHOT_STALE,
                    "state_provider",
                    f"snapshot is {age:.2f} s old (limit {self._max_age:.2f} s)",
                ),
            )
        return snapshot, ()

    def _finish(
        self,
        request: WorkcellRequest,
        call_id: str,
        stage: Stage,
        outcome: Outcome,
        findings: tuple[Finding, ...],
        gate_decision: GateDecision | None,
        snapshot: CellSnapshot | None,
        reason: str | None = None,
    ) -> WorkcellDecision:
        if reason is None and findings:
            reason = "; ".join(f"{finding.code.value}: {finding.detail}" for finding in findings)
        decision = WorkcellDecision(
            request_kind=request_kind(request),
            tool_call_id=call_id,
            stage=stage,
            outcome=outcome,
            findings=findings,
            gate=gate_decision,
            snapshot_digest=snapshot.digest() if snapshot is not None else None,
            config_hash=self._config_hash,
            rush=bool(getattr(request, "rush", False)),
            reason=reason,
        )
        self.journal.append(self._record(decision, request))
        return decision

    def _record(self, decision: WorkcellDecision, request: WorkcellRequest) -> dict[str, Any]:
        gate = decision.gate
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "cell_id": self.profile.cell_id,
            "config_hash": decision.config_hash,
            "tool_call_id": decision.tool_call_id,
            "request_kind": decision.request_kind,
            "request": json_safe(request_summary(request)),
            "rush": decision.rush,
            "stage": decision.stage,
            "outcome": decision.outcome.value,
            "executed": gate.executed if gate is not None else False,
            "findings": [finding.to_dict() for finding in decision.findings],
            "snapshot_digest": decision.snapshot_digest,
            "gate": None
            if gate is None
            else {
                "verdict": gate.verdict,
                "receipt_id": gate.receipt_id,
                "kid": gate.kid,
                "receipt_class": gate.receipt_class,
                "receipt_verified": gate.receipt_verified,
                "steering": gate.steering,
                "latency_ms": round(gate.latency_ms, 3),
            },
            "reason": decision.reason,
        }
