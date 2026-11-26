"""Workcell gate: application- and cell-level supervisory checks in front of RoboticsGate.

Hardware-agnostic. Cell state arrives through :class:`StateProvider` adapters (MES,
PLC, OPC UA, SCADA); evidence tokens are verified with ramen foundry's
``Ed25519TokenVerifier``; live verdicts come from ``RoboticsGate`` on the
``ramen__industrial_iot_actuation_invariance`` bundle. Every decision is journaled in
a hash chain and can be exported as a tamper-evident dossier.
"""

from .dossier import (
    DOSSIER_SCHEMA,
    DossierError,
    DossierManifest,
    export_dossier,
    render_markdown,
    verify_dossier,
)
from .gate import WorkcellDecision, WorkcellGate
from .issuer import EvidenceIssuer
from .journal import JOURNAL_SCHEMA, WorkcellJournal, read_entries, verify_chain
from .prechecks import Finding, FindingCode, Precheck, default_prechecks, run_prechecks
from .profile import CellProfile, EgressPolicy, OperatingMode, PlcEnvelope, ProfileError, SpeedEnvelope
from .requests import (
    BatchStart,
    CellMotion,
    RecipeExport,
    SafetyParameterChange,
    SetpointChange,
    WorkcellRequest,
    to_dispatch,
)
from .state import CellSnapshot, SensorReading, StateProvider, StaticStateProvider

__all__ = [
    "DOSSIER_SCHEMA",
    "JOURNAL_SCHEMA",
    "BatchStart",
    "CellMotion",
    "CellProfile",
    "CellSnapshot",
    "DossierError",
    "DossierManifest",
    "EgressPolicy",
    "EvidenceIssuer",
    "Finding",
    "FindingCode",
    "OperatingMode",
    "PlcEnvelope",
    "Precheck",
    "ProfileError",
    "RecipeExport",
    "SafetyParameterChange",
    "SensorReading",
    "SetpointChange",
    "SpeedEnvelope",
    "StateProvider",
    "StaticStateProvider",
    "WorkcellDecision",
    "WorkcellGate",
    "WorkcellJournal",
    "WorkcellRequest",
    "default_prechecks",
    "export_dossier",
    "read_entries",
    "render_markdown",
    "run_prechecks",
    "to_dispatch",
    "verify_chain",
    "verify_dossier",
]
