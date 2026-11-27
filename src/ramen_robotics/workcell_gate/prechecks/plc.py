"""PLC setpoint envelope: declared tag, inclusive bounds, positive ramp and slew limit."""

from __future__ import annotations

import math
from collections.abc import Sequence

from ..profile import CellProfile
from ..requests import SetpointChange, WorkcellRequest
from ..state import CellSnapshot
from .base import Finding, FindingCode


class PlcEnvelopeCheck:
    """Hold setpoint changes outside the integrator's envelope (for example a freezer zone above -18 °C)."""

    name = "plc_envelope"

    def applies_to(self, request: WorkcellRequest) -> bool:
        return isinstance(request, SetpointChange)

    def check(
        self, request: WorkcellRequest, snapshot: CellSnapshot, profile: CellProfile
    ) -> Sequence[Finding]:
        assert isinstance(request, SetpointChange)
        envelope = profile.plc_envelope(request.node_id, request.tag)
        label = f"{request.node_id}/{request.tag}"
        if envelope is None:
            return (Finding(FindingCode.PLC_UNKNOWN_TAG, self.name, f"{label} has no declared envelope"),)
        current = snapshot.plc_values.get(envelope.key)
        if current is None or not math.isfinite(current):
            return (Finding(FindingCode.SENSOR_STALE, self.name, f"{label}: no current value reported"),)
        target, ramp = request.target_val, request.ramp_rate_sec
        if not math.isfinite(target):
            return (Finding(FindingCode.PLC_OUT_OF_ENVELOPE, self.name, f"{label}: target is not finite"),)
        findings: list[Finding] = []
        units = envelope.engineering_units
        if envelope.min_val is not None and target < envelope.min_val:
            findings.append(
                Finding(
                    FindingCode.PLC_OUT_OF_ENVELOPE,
                    self.name,
                    f"{label}: {target} {units} below {envelope.min_val}",
                )
            )
        if envelope.max_val is not None and target > envelope.max_val:
            findings.append(
                Finding(
                    FindingCode.PLC_OUT_OF_ENVELOPE,
                    self.name,
                    f"{label}: {target} {units} above {envelope.max_val}",
                )
            )
        move = abs(target - current)
        if move > 0:
            if not (math.isfinite(ramp) and ramp > 0):
                findings.append(
                    Finding(FindingCode.PLC_SLEW_EXCEEDED, self.name, f"{label}: ramp_rate_sec must be > 0")
                )
            elif move / ramp > envelope.max_slew_per_s:
                findings.append(
                    Finding(
                        FindingCode.PLC_SLEW_EXCEEDED,
                        self.name,
                        f"{label}: slew {move / ramp:.4f} {units}/s exceeds {envelope.max_slew_per_s}",
                    )
                )
        return tuple(findings)
