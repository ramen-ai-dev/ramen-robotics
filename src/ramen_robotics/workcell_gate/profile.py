"""Static workcell configuration: the robot application, its envelopes and its trusted issuers.

A :class:`CellProfile` describes one robot cell the way ISO 10218-2:2025 does: as an
application with its safeguards and collaborative operation modes, not as a bare
manipulator. Runtime state (operating mode, sensor health, mounted tooling) lives in
:class:`~ramen_robotics.workcell_gate.state.CellSnapshot`.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

OperatingMode = Literal["automatic", "manual_reduced_speed", "manual_high_speed", "maintenance"]
OPERATING_MODES: tuple[OperatingMode, ...] = (
    "automatic",
    "manual_reduced_speed",
    "manual_high_speed",
    "maintenance",
)


class ProfileError(ValueError):
    """The cell profile is inconsistent. Raised at construction, before any dispatch."""


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class PlcEnvelope:
    """Inclusive bounds and slew limit for one PLC tag the cell may change."""

    node_id: str
    tag: str
    engineering_units: str
    operating_envelope_id: str
    min_val: float | None = None
    max_val: float | None = None
    max_slew_per_s: float = math.inf
    asset_criticality: str = "HIGH"

    def __post_init__(self) -> None:
        for name in ("node_id", "tag", "engineering_units", "operating_envelope_id", "asset_criticality"):
            if not str(getattr(self, name)).strip():
                raise ProfileError(f"PlcEnvelope.{name} must be non-blank")
        for name in ("min_val", "max_val"):
            value = getattr(self, name)
            if value is not None and not _finite(value):
                raise ProfileError(f"PlcEnvelope.{name} must be finite or None")
        if self.min_val is not None and self.max_val is not None and self.min_val > self.max_val:
            raise ProfileError("PlcEnvelope.min_val must not exceed max_val")
        if not (
            self.max_slew_per_s == math.inf or (_finite(self.max_slew_per_s) and self.max_slew_per_s > 0)
        ):
            raise ProfileError("PlcEnvelope.max_slew_per_s must be > 0")

    @property
    def key(self) -> tuple[str, str]:
        return (self.node_id, self.tag)


@dataclass(frozen=True)
class SpeedEnvelope:
    """Local speed ceiling and separation floor for one operating mode of the application."""

    mode: OperatingMode
    max_tcp_speed_mps: float
    min_separation_m: float

    def __post_init__(self) -> None:
        if self.mode not in OPERATING_MODES:
            raise ProfileError(f"unknown operating mode: {self.mode!r}")
        if not _finite(self.max_tcp_speed_mps) or self.max_tcp_speed_mps < 0:
            raise ProfileError("SpeedEnvelope.max_tcp_speed_mps must be finite and >= 0")
        if not _finite(self.min_separation_m) or self.min_separation_m < 0:
            raise ProfileError("SpeedEnvelope.min_separation_m must be finite and >= 0")


@dataclass(frozen=True)
class EgressPolicy:
    """Where recipe and process data may go. Everything not listed is denied."""

    allowed_destinations: frozenset[str] = frozenset()
    classified_fields: frozenset[str] = frozenset()
    classified_destinations: frozenset[str] = frozenset()


@dataclass(frozen=True)
class CellProfile:
    """One robot cell: application, safeguards, envelopes, recipes and pinned evidence issuers.

    Issuer maps hold ``kid -> raw 32-byte Ed25519 public key in hex`` (the format
    :class:`ramen_foundry.Ed25519TokenVerifier` pins). Key custody and token
    issuance stay with the host's MES, sanitation and lockout systems.
    """

    cell_id: str
    application: str
    robot_ids: tuple[str, ...]
    safeguards: tuple[str, ...] = ()
    collaborative_operation: tuple[str, ...] = ()
    speed_envelopes: tuple[SpeedEnvelope, ...] = ()
    plc_envelopes: tuple[PlcEnvelope, ...] = ()
    safety_tags: tuple[tuple[str, str], ...] = ()
    allergen_matrix: Mapping[str, frozenset[str]] = field(default_factory=dict)
    recipe_tooling: Mapping[str, frozenset[str]] = field(default_factory=dict)
    egress: EgressPolicy = field(default_factory=EgressPolicy)
    work_order_issuers: Mapping[str, str] = field(default_factory=dict)
    sanitation_issuers: Mapping[str, str] = field(default_factory=dict)
    key_evidence_issuers: Mapping[str, str] = field(default_factory=dict)
    required_sensors: tuple[str, ...] = ()
    sensor_min_confidence: float = 0.8
    sensor_max_age_s: float = 2.0
    collaborative_zone_m: float = 1.0

    def __post_init__(self) -> None:
        if not self.cell_id.strip() or not self.application.strip():
            raise ProfileError("cell_id and application must be non-blank")
        if not self.robot_ids:
            raise ProfileError("a cell needs at least one robot id")
        modes = [envelope.mode for envelope in self.speed_envelopes]
        if len(modes) != len(set(modes)):
            raise ProfileError("one SpeedEnvelope per operating mode")
        keys = [envelope.key for envelope in self.plc_envelopes]
        if len(keys) != len(set(keys)):
            raise ProfileError("one PlcEnvelope per (node_id, tag)")
        if not (0.0 <= self.sensor_min_confidence <= 1.0):
            raise ProfileError("sensor_min_confidence must be within [0, 1]")
        for name in ("sensor_max_age_s", "collaborative_zone_m"):
            value = getattr(self, name)
            if not _finite(value) or value <= 0:
                raise ProfileError(f"{name} must be finite and > 0")
        for recipe in self.recipe_tooling:
            if recipe not in self.allergen_matrix:
                raise ProfileError(f"recipe {recipe!r} has tooling but no allergen declaration")

    def speed_envelope(self, mode: str) -> SpeedEnvelope | None:
        return next((envelope for envelope in self.speed_envelopes if envelope.mode == mode), None)

    def plc_envelope(self, node_id: str, tag: str) -> PlcEnvelope | None:
        return next((envelope for envelope in self.plc_envelopes if envelope.key == (node_id, tag)), None)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready description; sets are sorted so the result is canonical."""
        return {
            "cell_id": self.cell_id,
            "application": self.application,
            "robot_ids": list(self.robot_ids),
            "safeguards": list(self.safeguards),
            "collaborative_operation": list(self.collaborative_operation),
            "speed_envelopes": [
                {
                    "mode": e.mode,
                    "max_tcp_speed_mps": e.max_tcp_speed_mps,
                    "min_separation_m": e.min_separation_m,
                }
                for e in self.speed_envelopes
            ],
            "plc_envelopes": [
                {
                    "node_id": e.node_id,
                    "tag": e.tag,
                    "engineering_units": e.engineering_units,
                    "operating_envelope_id": e.operating_envelope_id,
                    "min_val": e.min_val,
                    "max_val": e.max_val,
                    "max_slew_per_s": None if e.max_slew_per_s == math.inf else e.max_slew_per_s,
                    "asset_criticality": e.asset_criticality,
                }
                for e in self.plc_envelopes
            ],
            "safety_tags": [list(pair) for pair in self.safety_tags],
            "allergen_matrix": {k: sorted(v) for k, v in sorted(self.allergen_matrix.items())},
            "recipe_tooling": {k: sorted(v) for k, v in sorted(self.recipe_tooling.items())},
            "egress": {
                "allowed_destinations": sorted(self.egress.allowed_destinations),
                "classified_fields": sorted(self.egress.classified_fields),
                "classified_destinations": sorted(self.egress.classified_destinations),
            },
            "work_order_issuers": dict(sorted(self.work_order_issuers.items())),
            "sanitation_issuers": dict(sorted(self.sanitation_issuers.items())),
            "key_evidence_issuers": dict(sorted(self.key_evidence_issuers.items())),
            "required_sensors": list(self.required_sensors),
            "sensor_min_confidence": self.sensor_min_confidence,
            "sensor_max_age_s": self.sensor_max_age_s,
            "collaborative_zone_m": self.collaborative_zone_m,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CellProfile:
        """Rebuild a profile from :meth:`to_dict` output (for example a run's ``profile.json``)."""
        egress = data.get("egress") or {}
        return cls(
            cell_id=data["cell_id"],
            application=data["application"],
            robot_ids=tuple(data["robot_ids"]),
            safeguards=tuple(data.get("safeguards", ())),
            collaborative_operation=tuple(data.get("collaborative_operation", ())),
            speed_envelopes=tuple(SpeedEnvelope(**item) for item in data.get("speed_envelopes", ())),
            plc_envelopes=tuple(
                PlcEnvelope(
                    **{
                        **item,
                        "max_slew_per_s": math.inf
                        if item.get("max_slew_per_s") is None
                        else item["max_slew_per_s"],
                    }
                )
                for item in data.get("plc_envelopes", ())
            ),
            safety_tags=tuple((str(pair[0]), str(pair[1])) for pair in data.get("safety_tags", ())),
            allergen_matrix={k: frozenset(v) for k, v in (data.get("allergen_matrix") or {}).items()},
            recipe_tooling={k: frozenset(v) for k, v in (data.get("recipe_tooling") or {}).items()},
            egress=EgressPolicy(
                allowed_destinations=frozenset(egress.get("allowed_destinations", ())),
                classified_fields=frozenset(egress.get("classified_fields", ())),
                classified_destinations=frozenset(egress.get("classified_destinations", ())),
            ),
            work_order_issuers=dict(data.get("work_order_issuers") or {}),
            sanitation_issuers=dict(data.get("sanitation_issuers") or {}),
            key_evidence_issuers=dict(data.get("key_evidence_issuers") or {}),
            required_sensors=tuple(data.get("required_sensors", ())),
            sensor_min_confidence=float(data.get("sensor_min_confidence", 0.8)),
            sensor_max_age_s=float(data.get("sensor_max_age_s", 2.0)),
            collaborative_zone_m=float(data.get("collaborative_zone_m", 1.0)),
        )

    def config_hash(self) -> str:
        """SHA-256 of the canonical profile description. Recorded with every decision."""
        return hashlib.sha256(canonical_json(self.to_dict()).encode("utf-8")).hexdigest()


def canonical_json(value: Any) -> str:
    """Sorted-key, compact JSON used for every digest in this module."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def json_safe(value: Any) -> Any:
    """Return ``value`` with non-finite floats replaced by their string form (strict JSON)."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, Mapping):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value
