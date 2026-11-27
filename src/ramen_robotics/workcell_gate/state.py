"""Runtime workcell state and the provider interface that MES, PLC and SCADA adapters implement."""

from __future__ import annotations

import dataclasses
import hashlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from .profile import OperatingMode, canonical_json, json_safe


@dataclass(frozen=True)
class SensorReading:
    """Health of one upstream perception or sensing channel, as reported by that system."""

    sensor_id: str
    kind: str
    confidence: float
    age_s: float
    faults: tuple[str, ...] = ()


@dataclass(frozen=True)
class CellSnapshot:
    """What the cell reports at one instant. ``taken_at`` is Unix seconds.

    ``nearest_human_m`` is ``None`` when the separation channel has no reading, which
    the gate treats as missing evidence, never as "nobody present".
    """

    taken_at: float
    mode: OperatingMode
    mounted_gripper_id: str
    active_recipe_id: str
    previous_recipe_id: str | None = None
    sanitation_token: str | None = field(default=None, repr=False)
    safeguards_ok: Mapping[str, bool] = field(default_factory=dict)
    loto_active: bool = False
    nearest_human_m: float | None = None
    plc_values: Mapping[tuple[str, str], float] = field(default_factory=dict)
    sensors: tuple[SensorReading, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return json_safe(  # type: ignore[no-any-return]
            {
                "taken_at": self.taken_at,
                "mode": self.mode,
                "mounted_gripper_id": self.mounted_gripper_id,
                "active_recipe_id": self.active_recipe_id,
                "previous_recipe_id": self.previous_recipe_id,
                "sanitation_token_sha256": _digest(self.sanitation_token),
                "safeguards_ok": dict(sorted(self.safeguards_ok.items())),
                "loto_active": self.loto_active,
                "nearest_human_m": self.nearest_human_m,
                "plc_values": {
                    f"{node}/{tag}": value for (node, tag), value in sorted(self.plc_values.items())
                },
                "sensors": [dataclasses.asdict(sensor) for sensor in self.sensors],
            }
        )

    def digest(self) -> str:
        """SHA-256 of the canonical snapshot (tokens appear only as their own digests)."""
        return hashlib.sha256(canonical_json(self.to_dict()).encode("utf-8")).hexdigest()


def _digest(value: str | None) -> str | None:
    return None if value is None else hashlib.sha256(value.encode("utf-8")).hexdigest()


class StateProvider(Protocol):
    """Source of cell state. Implement this for an MES, PLC, OPC UA or SCADA adapter."""

    def snapshot(self) -> CellSnapshot: ...


class StaticStateProvider:
    """In-memory provider for tests, simulators and demos.

    Each :meth:`snapshot` returns the current state re-stamped with ``clock()`` unless
    ``restamp`` is false. :meth:`set` replaces the state; :meth:`update` changes fields.
    """

    def __init__(
        self, state: CellSnapshot, *, clock: Callable[[], float] = time.time, restamp: bool = True
    ) -> None:
        self._state = state
        self._clock = clock
        self._restamp = restamp

    def set(self, state: CellSnapshot) -> None:
        self._state = state

    def update(self, **changes: Any) -> None:
        self._state = dataclasses.replace(self._state, **changes)

    def snapshot(self) -> CellSnapshot:
        if self._restamp:
            return dataclasses.replace(self._state, taken_at=self._clock())
        return self._state
