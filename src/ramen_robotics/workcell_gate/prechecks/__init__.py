"""Deterministic workcell pre-checks. Any finding holds the request before an evaluation request."""

from __future__ import annotations

from .base import Finding, FindingCode, Precheck, run_prechecks
from .changeover import ChangeoverCheck
from .egress import EgressCheck
from .evidence import verify_token
from .plc import PlcEnvelopeCheck
from .safety import MotionEnvelopeCheck, SafetyParameterCheck, SafetyStateCheck
from .sensors import SensorHealthCheck
from .work_order import WorkOrderCheck


def default_prechecks() -> tuple[Precheck, ...]:
    """The v1 check set, in a fixed order so findings are reproducible."""
    return (
        WorkOrderCheck(),
        ChangeoverCheck(),
        SensorHealthCheck(),
        SafetyStateCheck(),
        MotionEnvelopeCheck(),
        PlcEnvelopeCheck(),
        SafetyParameterCheck(),
        EgressCheck(),
    )


__all__ = [
    "ChangeoverCheck",
    "EgressCheck",
    "Finding",
    "FindingCode",
    "MotionEnvelopeCheck",
    "PlcEnvelopeCheck",
    "Precheck",
    "SafetyParameterCheck",
    "SafetyStateCheck",
    "SensorHealthCheck",
    "WorkOrderCheck",
    "default_prechecks",
    "run_prechecks",
    "verify_token",
]
