"""Degraded-sensor hold: no motion dispatch on perception the upstream system reports as unreliable.

This check reads health that the vision or sensing system itself reports (confidence,
age, fault flags). It complements those systems and the safety-rated devices of the
cell; it does not inspect images or replace any safety function.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..profile import CellProfile
from ..requests import BatchStart, CellMotion, WorkcellRequest
from ..state import CellSnapshot
from .base import Finding, FindingCode


class SensorHealthCheck:
    name = "sensor_health"

    def applies_to(self, request: WorkcellRequest) -> bool:
        return isinstance(request, (BatchStart, CellMotion))

    def check(
        self, request: WorkcellRequest, snapshot: CellSnapshot, profile: CellProfile
    ) -> Sequence[Finding]:
        findings: list[Finding] = []
        readings = {reading.sensor_id: reading for reading in snapshot.sensors}
        for sensor_id in profile.required_sensors:
            if sensor_id not in readings:
                findings.append(Finding(FindingCode.SENSOR_STALE, self.name, f"{sensor_id}: no reading"))
        for reading in snapshot.sensors:
            if reading.faults:
                findings.append(
                    Finding(
                        FindingCode.SENSOR_DEGRADED,
                        self.name,
                        f"{reading.sensor_id}: reports {', '.join(reading.faults)}",
                    )
                )
            if not (reading.confidence >= profile.sensor_min_confidence):  # also catches NaN
                findings.append(
                    Finding(
                        FindingCode.SENSOR_DEGRADED,
                        self.name,
                        f"{reading.sensor_id}: confidence {reading.confidence:.2f} "
                        f"below {profile.sensor_min_confidence:.2f}",
                    )
                )
            if not (reading.age_s <= profile.sensor_max_age_s):
                findings.append(
                    Finding(
                        FindingCode.SENSOR_STALE,
                        self.name,
                        f"{reading.sensor_id}: reading is {reading.age_s:.1f} s old "
                        f"(limit {profile.sensor_max_age_s:.1f} s)",
                    )
                )
        if snapshot.nearest_human_m is None:
            findings.append(Finding(FindingCode.SENSOR_STALE, self.name, "no human-separation reading"))
        return tuple(findings)
