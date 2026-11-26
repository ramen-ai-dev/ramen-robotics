"""Typed supervisory requests and their mapping to ramen foundry's industrial tool payloads.

Every live-evaluated request maps to exactly one of the three tools that
``IndustrialAutomationAgent`` registers. Cell context travels in the declared payload
fields (``scene_context_id``, ``active_hazard_flags``); no undeclared keys are added.
"""

from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass
from typing import Any

from ramen_foundry import ManipulationActionType

from .profile import CellProfile
from .state import CellSnapshot

HAZARD_OPERATOR_IN_ZONE = "OPERATOR_IN_COLLABORATIVE_ZONE"
_TOKEN_FIELDS = frozenset({"work_order_token", "key_evidence_token"})


@dataclass(frozen=True)
class BatchStart:
    """Start a production batch: the first released segment of a signed work order.

    ``rush`` is recorded in the journal and never changes a check or the evaluated payload.
    """

    work_order_token: str = dataclasses.field(repr=False)
    work_order_id: str
    recipe_id: str
    lot_id: str
    quantity: int
    robot_id: str
    target_object: str
    destination_target: str
    commanded_velocity_mps: float
    commanded_force_nm: float
    rush: bool = False


@dataclass(frozen=True)
class CellMotion:
    """One supervisory manipulation segment inside a running batch."""

    robot_id: str
    action_type: ManipulationActionType
    target_object: str
    destination_target: str
    commanded_velocity_mps: float
    commanded_force_nm: float
    ref: str
    active_hazard_flags: tuple[str, ...] = ()
    rush: bool = False


@dataclass(frozen=True)
class SetpointChange:
    """Ramp one declared PLC tag to ``target_val``; ``current_val`` comes from the snapshot."""

    node_id: str
    tag: str
    target_val: float
    ramp_rate_sec: float
    rush: bool = False


@dataclass(frozen=True)
class SafetyParameterChange:
    """Change a safety-function parameter (mute, bypass, lockout override).

    ``physical_key_interlock_verified=True`` must be backed by a signed
    ``key_evidence_token`` from a pinned lockout issuer; the boolean alone is not evidence.
    """

    node_id: str
    tag: str
    mutation_type: str
    requested_value: str | float
    duration_sec: float
    physical_key_interlock_verified: bool
    management_of_change_id: str
    key_evidence_token: str | None = dataclasses.field(default=None, repr=False)
    rush: bool = False


@dataclass(frozen=True)
class RecipeExport:
    """Send recipe or process data out of the cell. Checked locally only in v1."""

    recipe_id: str
    destination: str
    fields: tuple[str, ...]
    rush: bool = False


WorkcellRequest = BatchStart | CellMotion | SetpointChange | SafetyParameterChange | RecipeExport


def request_kind(request: WorkcellRequest) -> str:
    return type(request).__name__


def request_summary(request: WorkcellRequest) -> dict[str, Any]:
    """JSON-ready request description. Tokens are replaced by their SHA-256 digests."""
    summary: dict[str, Any] = {}
    for item in dataclasses.fields(request):
        value = getattr(request, item.name)
        if item.name in _TOKEN_FIELDS:
            summary[f"{item.name}_sha256"] = (
                None if value is None else hashlib.sha256(str(value).encode("utf-8")).hexdigest()
            )
        elif isinstance(value, tuple):
            summary[item.name] = list(value)
        else:
            summary[item.name] = value
    return summary


def scene_context_id(profile: CellProfile, snapshot: CellSnapshot, ref: str) -> str:
    """``<cell>/<mode>/cfg-<12 hex>/<ref>``: binds the evaluated call to the cell configuration."""
    return f"{profile.cell_id}/{snapshot.mode}/cfg-{profile.config_hash()[:12]}/{ref}"


def observed_hazards(profile: CellProfile, snapshot: CellSnapshot) -> list[str]:
    """Hazard flags the gate derives from the snapshot itself."""
    flags: list[str] = []
    if snapshot.nearest_human_m is not None and snapshot.nearest_human_m < profile.collaborative_zone_m:
        flags.append(HAZARD_OPERATOR_IN_ZONE)
    return flags


def to_dispatch(
    request: WorkcellRequest, snapshot: CellSnapshot, profile: CellProfile
) -> tuple[str, dict[str, Any]]:
    """Return ``(tool_name, payload)`` for a live-evaluated request.

    Call only after the pre-checks are clean: it assumes a separation reading and a
    known PLC tag. Raises ``TypeError`` for :class:`RecipeExport`, which has no tool.
    """
    if isinstance(request, (BatchStart, CellMotion)):
        if snapshot.nearest_human_m is None:
            raise ValueError("no separation reading")
        flags = observed_hazards(profile, snapshot)
        if isinstance(request, CellMotion):
            flags += [flag for flag in request.active_hazard_flags if flag not in flags]
            action: str = request.action_type
            ref = request.ref
        else:
            action = "PICK_AND_PLACE"
            ref = f"{request.work_order_id}/{request.lot_id}"
        return "dispatch_manipulation", {
            "robot_id": request.robot_id,
            "action_type": action,
            "target_object": request.target_object,
            "destination_target": request.destination_target,
            "commanded_velocity_mps": float(request.commanded_velocity_mps),
            "commanded_force_nm": float(request.commanded_force_nm),
            "human_proximity_meters": float(snapshot.nearest_human_m),
            "active_hazard_flags": flags,
            "scene_context_id": scene_context_id(profile, snapshot, ref),
        }
    if isinstance(request, SetpointChange):
        envelope = profile.plc_envelope(request.node_id, request.tag)
        if envelope is None:
            raise ValueError("undeclared PLC tag")
        return "adjust_plc_setpoint", {
            "node_id": request.node_id,
            "tag": request.tag,
            "target_val": float(request.target_val),
            "current_val": float(snapshot.plc_values[envelope.key]),
            "ramp_rate_sec": float(request.ramp_rate_sec),
            "engineering_units": envelope.engineering_units,
            "asset_criticality": envelope.asset_criticality,
            "operating_envelope_id": envelope.operating_envelope_id,
        }
    if isinstance(request, SafetyParameterChange):
        return "mutate_safety_parameter", {
            "node_id": request.node_id,
            "tag": request.tag,
            "mutation_type": request.mutation_type,
            "requested_value": request.requested_value,
            "duration_sec": request.duration_sec,
            "physical_key_interlock_verified": request.physical_key_interlock_verified,
            "management_of_change_id": request.management_of_change_id,
        }
    raise TypeError(f"{request_kind(request)} has no live tool in this version")
