"""People and safety: lockout, safeguard state, operating mode, speed and separation envelopes."""

from __future__ import annotations

import math
from collections.abc import Sequence

from ..profile import CellProfile
from ..requests import BatchStart, CellMotion, SafetyParameterChange, SetpointChange, WorkcellRequest
from ..state import CellSnapshot
from .base import Finding, FindingCode
from .evidence import verify_token


class SafetyStateCheck:
    """No motion or setpoint change while a lockout is active or a safeguard reports a fault."""

    name = "safety_state"

    def applies_to(self, request: WorkcellRequest) -> bool:
        return isinstance(request, (BatchStart, CellMotion, SetpointChange))

    def check(
        self, request: WorkcellRequest, snapshot: CellSnapshot, profile: CellProfile
    ) -> Sequence[Finding]:
        findings: list[Finding] = []
        if snapshot.loto_active:
            findings.append(Finding(FindingCode.LOTO_ACTIVE, self.name, "a lockout is active on the cell"))
        if isinstance(request, (BatchStart, CellMotion)):
            for safeguard in profile.safeguards:
                if snapshot.safeguards_ok.get(safeguard) is not True:
                    findings.append(
                        Finding(
                            FindingCode.SAFEGUARD_FAULT, self.name, f"{safeguard} is not reporting healthy"
                        )
                    )
        return tuple(findings)


class MotionEnvelopeCheck:
    """Robot identity, operating mode and the local speed and separation envelope of the application."""

    name = "motion_envelope"

    def applies_to(self, request: WorkcellRequest) -> bool:
        return isinstance(request, (BatchStart, CellMotion))

    def check(
        self, request: WorkcellRequest, snapshot: CellSnapshot, profile: CellProfile
    ) -> Sequence[Finding]:
        assert isinstance(request, (BatchStart, CellMotion))
        findings: list[Finding] = []
        if request.robot_id not in profile.robot_ids:
            findings.append(
                Finding(FindingCode.ROBOT_UNKNOWN, self.name, f"{request.robot_id} is not in this cell")
            )
        if isinstance(request, BatchStart) and snapshot.mode != "automatic":
            findings.append(
                Finding(
                    FindingCode.MODE_CONFLICT,
                    self.name,
                    f"batch start needs automatic mode, cell is {snapshot.mode}",
                )
            )
        envelope = profile.speed_envelope(snapshot.mode)
        speed = request.commanded_velocity_mps
        if envelope is None:
            findings.append(
                Finding(FindingCode.MODE_CONFLICT, self.name, f"no speed envelope for {snapshot.mode}")
            )
        else:
            if not (math.isfinite(speed) and speed <= envelope.max_tcp_speed_mps):
                findings.append(
                    Finding(
                        FindingCode.MODE_CONFLICT,
                        self.name,
                        f"commanded {speed} m/s exceeds {envelope.max_tcp_speed_mps} m/s for {snapshot.mode}",
                    )
                )
            separation = snapshot.nearest_human_m
            if separation is not None and not (separation >= envelope.min_separation_m):
                findings.append(
                    Finding(
                        FindingCode.SEPARATION_BELOW_ENVELOPE,
                        self.name,
                        f"nearest person at {separation} m, envelope floor {envelope.min_separation_m} m",
                    )
                )
        return tuple(findings)


class SafetyParameterCheck:
    """Safety-function writes must name a declared tag, a finite duration and real key evidence.

    A request that asserts ``physical_key_interlock_verified`` must carry a token from a
    pinned lockout issuer binding the cell, node, tag and management-of-change id. A request
    without that assertion goes to the live bundle, which judges it.
    """

    name = "safety_parameter"

    def applies_to(self, request: WorkcellRequest) -> bool:
        return isinstance(request, SafetyParameterChange)

    def check(
        self, request: WorkcellRequest, snapshot: CellSnapshot, profile: CellProfile
    ) -> Sequence[Finding]:
        assert isinstance(request, SafetyParameterChange)
        findings: list[Finding] = []
        if (request.node_id, request.tag) not in profile.safety_tags:
            findings.append(
                Finding(
                    FindingCode.PLC_UNKNOWN_TAG,
                    self.name,
                    f"{request.node_id}/{request.tag} is not a declared safety tag",
                )
            )
        duration = request.duration_sec
        if isinstance(duration, bool) or not (math.isfinite(duration) and duration > 0):
            findings.append(
                Finding(FindingCode.SAFETY_REQUEST_INVALID, self.name, "duration_sec must be finite and > 0")
            )
        if not request.management_of_change_id.strip() or not request.mutation_type.strip():
            findings.append(
                Finding(
                    FindingCode.SAFETY_REQUEST_INVALID,
                    self.name,
                    "mutation_type and management_of_change_id are required",
                )
            )
        if request.physical_key_interlock_verified:
            valid, reason = verify_token(
                request.key_evidence_token,
                profile.key_evidence_issuers,
                {
                    "cell_id": profile.cell_id,
                    "node_id": request.node_id,
                    "tag": request.tag,
                    "management_of_change_id": request.management_of_change_id,
                },
            )
            if not valid:
                findings.append(Finding(FindingCode.KEY_EVIDENCE_UNVERIFIED, self.name, str(reason)))
        return tuple(findings)
