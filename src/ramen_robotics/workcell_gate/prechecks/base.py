"""Finding codes, the pre-check protocol and the runner."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from ..profile import CellProfile
from ..requests import WorkcellRequest
from ..state import CellSnapshot


class FindingCode(str, Enum):
    """Why a request was held locally. Every finding is a hold; none is a warning."""

    WORK_ORDER_UNVERIFIED = "WORK_ORDER_UNVERIFIED"
    WORK_ORDER_MISMATCH = "WORK_ORDER_MISMATCH"
    RECIPE_MISMATCH = "RECIPE_MISMATCH"
    SANITATION_UNVERIFIED = "SANITATION_UNVERIFIED"
    ALLERGEN_CONFLICT = "ALLERGEN_CONFLICT"
    TOOLING_MISMATCH = "TOOLING_MISMATCH"
    SENSOR_DEGRADED = "SENSOR_DEGRADED"
    SENSOR_STALE = "SENSOR_STALE"
    SNAPSHOT_STALE = "SNAPSHOT_STALE"
    LOTO_ACTIVE = "LOTO_ACTIVE"
    SAFEGUARD_FAULT = "SAFEGUARD_FAULT"
    MODE_CONFLICT = "MODE_CONFLICT"
    SEPARATION_BELOW_ENVELOPE = "SEPARATION_BELOW_ENVELOPE"
    ROBOT_UNKNOWN = "ROBOT_UNKNOWN"
    PLC_OUT_OF_ENVELOPE = "PLC_OUT_OF_ENVELOPE"
    PLC_SLEW_EXCEEDED = "PLC_SLEW_EXCEEDED"
    PLC_UNKNOWN_TAG = "PLC_UNKNOWN_TAG"
    SAFETY_REQUEST_INVALID = "SAFETY_REQUEST_INVALID"
    KEY_EVIDENCE_UNVERIFIED = "KEY_EVIDENCE_UNVERIFIED"
    EGRESS_DENIED = "EGRESS_DENIED"
    TOOL_NOT_REGISTERED = "TOOL_NOT_REGISTERED"
    PRECHECK_ERROR = "PRECHECK_ERROR"


@dataclass(frozen=True)
class Finding:
    """One local hold reason. ``detail`` names fields and limits, never token contents."""

    code: FindingCode
    check: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "check": self.check, "detail": self.detail}


class Precheck(Protocol):
    """A deterministic, side-effect-free check run before any evaluation request."""

    name: str

    def applies_to(self, request: WorkcellRequest) -> bool: ...

    def check(
        self, request: WorkcellRequest, snapshot: CellSnapshot, profile: CellProfile
    ) -> Sequence[Finding]: ...


def run_prechecks(
    prechecks: Sequence[Precheck], request: WorkcellRequest, snapshot: CellSnapshot, profile: CellProfile
) -> tuple[Finding, ...]:
    """Run every applicable check and return all findings. A check that raises is a hold."""
    findings: list[Finding] = []
    for precheck in prechecks:
        try:
            if not precheck.applies_to(request):
                continue
            findings.extend(precheck.check(request, snapshot, profile))
        except Exception as error:  # a failing check never lets a request through
            findings.append(
                Finding(FindingCode.PRECHECK_ERROR, getattr(precheck, "name", "?"), type(error).__name__)
            )
    return tuple(findings)
