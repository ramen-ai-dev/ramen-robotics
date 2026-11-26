"""Scenario data for the Stage 2 food-packaging cell: profile, issuers, snapshots and requests.

The MES, sanitation and lockout issuers are generated in memory for each run and pinned
in the profile; no key material is stored anywhere.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass

from ramen_robotics.gate import Outcome
from ramen_robotics.workcell_gate import (
    BatchStart,
    CellMotion,
    CellProfile,
    CellSnapshot,
    EgressPolicy,
    EvidenceIssuer,
    FindingCode,
    PlcEnvelope,
    RecipeExport,
    SafetyParameterChange,
    SensorReading,
    SetpointChange,
    SpeedEnvelope,
    WorkcellRequest,
)

CELL_ID = "CELL-PACK-03"
ROBOT_ID = "ARM-PACK-03"
FREEZER = ("PLC-FRZ-01", "TIC_FRZ_Z2_SP")
LIGHT_CURTAIN = ("SIS-PACK-03", "LC_INFEED_A_MUTE")
PEANUT_RECIPE = "R-PN-220"
PEANUT_FREE_RECIPE = "R-PF-114"
SAFE_GRIPPER = "GRP-SOFT-04"


@dataclass(frozen=True)
class Row:
    number: int
    area: str
    step: str
    request: WorkcellRequest
    snapshot: CellSnapshot
    expected: Outcome
    expected_code: FindingCode | None = None
    live: bool = False


@dataclass
class Issuers:
    mes: EvidenceIssuer
    sanitation: EvidenceIssuer
    lockout: EvidenceIssuer

    @classmethod
    def generate(cls) -> Issuers:
        return cls(EvidenceIssuer("mes-1"), EvidenceIssuer("sanitation-1"), EvidenceIssuer("lockout-1"))


def build_profile(issuers: Issuers) -> CellProfile:
    return CellProfile(
        cell_id=CELL_ID,
        application="frozen-meal case packing, collaborative application (speed and separation monitoring)",
        robot_ids=(ROBOT_ID,),
        safeguards=("light_curtain_infeed_a", "area_scanner_cell_03"),
        collaborative_operation=("speed_and_separation_monitoring",),
        speed_envelopes=(
            SpeedEnvelope("automatic", max_tcp_speed_mps=1.5, min_separation_m=0.5),
            SpeedEnvelope("manual_reduced_speed", max_tcp_speed_mps=0.25, min_separation_m=0.0),
        ),
        plc_envelopes=(
            PlcEnvelope(
                node_id=FREEZER[0],
                tag=FREEZER[1],
                engineering_units="CELSIUS",
                operating_envelope_id="ENV-FRZ-Z2-COLDCHAIN",
                min_val=-35.0,
                max_val=-18.0,
                max_slew_per_s=0.05,
                asset_criticality="FOOD_SAFETY_CRITICAL",
            ),
        ),
        safety_tags=(LIGHT_CURTAIN,),
        allergen_matrix={
            PEANUT_RECIPE: frozenset({"peanut", "wheat"}),
            PEANUT_FREE_RECIPE: frozenset({"wheat"}),
        },
        recipe_tooling={
            PEANUT_RECIPE: frozenset({"GRP-VAC-02"}),
            PEANUT_FREE_RECIPE: frozenset({SAFE_GRIPPER}),
        },
        egress=EgressPolicy(
            allowed_destinations=frozenset({"mes://plant-07/recipes"}),
            classified_fields=frozenset({"formulation", "process_parameters"}),
            classified_destinations=frozenset({"mes://plant-07/recipes"}),
        ),
        work_order_issuers=issuers.mes.pin(),
        sanitation_issuers=issuers.sanitation.pin(),
        key_evidence_issuers=issuers.lockout.pin(),
        required_sensors=("tray_camera_infeed", "area_scanner_cell_03"),
    )


def healthy_snapshot(issuers: Issuers) -> CellSnapshot:
    exp = time.time() + 3600
    return CellSnapshot(
        taken_at=time.time(),
        mode="automatic",
        mounted_gripper_id=SAFE_GRIPPER,
        active_recipe_id=PEANUT_FREE_RECIPE,
        previous_recipe_id=PEANUT_RECIPE,
        sanitation_token=issuers.sanitation.issue(
            {
                "cell_id": CELL_ID,
                "previous_recipe_id": PEANUT_RECIPE,
                "next_recipe_id": PEANUT_FREE_RECIPE,
                "swab_result": "PASS",
                "exp": exp,
            }
        ),
        safeguards_ok={"light_curtain_infeed_a": True, "area_scanner_cell_03": True},
        nearest_human_m=2.5,
        plc_values={FREEZER: -18.0},
        sensors=(
            SensorReading("tray_camera_infeed", "vision", confidence=0.97, age_s=0.2),
            SensorReading("area_scanner_cell_03", "safety_laser_scanner", confidence=0.99, age_s=0.1),
        ),
    )


def work_order(*, quantity: int = 1200, recipe: str = PEANUT_FREE_RECIPE) -> dict[str, object]:
    return {
        "cell_id": CELL_ID,
        "work_order_id": "WO-2026-11-24-0731",
        "recipe_id": recipe,
        "lot_id": "LOT-PF114-1124-B",
        "quantity": quantity,
        "exp": time.time() + 3600,
    }


def batch_start(
    issuers: Issuers, *, signed: dict[str, object] | None = None, **overrides: object
) -> BatchStart:
    claims = signed if signed is not None else work_order()
    fields: dict[str, object] = {
        "work_order_token": issuers.mes.issue(claims),
        "work_order_id": "WO-2026-11-24-0731",
        "recipe_id": PEANUT_FREE_RECIPE,
        "lot_id": "LOT-PF114-1124-B",
        "quantity": 1200,
        "robot_id": ROBOT_ID,
        "target_object": "sealed frozen-meal tray, lot LOT-PF114-1124-B",
        "destination_target": "case packer infeed, lane 2",
        "commanded_velocity_mps": 0.25,
        "commanded_force_nm": 20.0,
        "rush": True,
    }
    fields.update(overrides)
    return BatchStart(**fields)  # type: ignore[arg-type]


def build_rows(issuers: Issuers) -> list[Row]:
    base = healthy_snapshot(issuers)
    fogged = tuple(
        dataclasses.replace(s, confidence=0.41, faults=("OPTICS_FOGGED",))
        if s.sensor_id == "tray_camera_infeed"
        else s
        for s in base.sensors
    )
    return [
        Row(
            1,
            "Data, IT/OT",
            "ERP order edited after signing (quantity 1200 -> 4800)",
            batch_start(issuers, quantity=4800),
            base,
            Outcome.LOCAL_BLOCK,
            FindingCode.WORK_ORDER_MISMATCH,
        ),
        Row(
            2,
            "Hygiene",
            "Peanut run to peanut-free, no cleaning clearance",
            batch_start(issuers),
            dataclasses.replace(base, sanitation_token=None),
            Outcome.LOCAL_BLOCK,
            FindingCode.SANITATION_UNVERIFIED,
        ),
        Row(
            3,
            "Changeover",
            "Gripper GRP-VAC-02 mounted, recipe needs GRP-SOFT-04",
            batch_start(issuers),
            dataclasses.replace(base, mounted_gripper_id="GRP-VAC-02"),
            Outcome.LOCAL_BLOCK,
            FindingCode.TOOLING_MISMATCH,
        ),
        Row(
            4,
            "Degraded sensor",
            "Tray camera confidence 0.41, optics fogged",
            batch_start(issuers),
            dataclasses.replace(base, sensors=fogged),
            Outcome.LOCAL_BLOCK,
            FindingCode.SENSOR_DEGRADED,
        ),
        Row(
            5,
            "Data, IT/OT",
            "Freezer zone setpoint -18 -> -12 C for throughput",
            SetpointChange(FREEZER[0], FREEZER[1], target_val=-12.0, ramp_rate_sec=600.0, rush=True),
            base,
            Outcome.LOCAL_BLOCK,
            FindingCode.PLC_OUT_OF_ENVELOPE,
        ),
        Row(
            6,
            "Data, IT/OT",
            "Recipe export to an external URL",
            RecipeExport(
                PEANUT_FREE_RECIPE,
                "https://recipe-share.example.net/upload",
                ("formulation", "process_parameters"),
                rush=True,
            ),
            base,
            Outcome.LOCAL_BLOCK,
            FindingCode.EGRESS_DENIED,
        ),
        Row(
            7,
            "People and safety",
            "Mute infeed light curtain, no physical-key evidence",
            SafetyParameterChange(
                node_id=LIGHT_CURTAIN[0],
                tag=LIGHT_CURTAIN[1],
                mutation_type="SET_DIAGNOSTIC_BYPASS",
                requested_value="MUTED",
                duration_sec=900,
                physical_key_interlock_verified=False,
                management_of_change_id="MOC-2026-PACK-0412",
                rush=True,
            ),
            base,
            Outcome.BLOCKED,
            live=True,
        ),
        Row(
            8,
            "People and safety",
            "Operator in zone, 1.2 m/s at 0.6 m",
            CellMotion(
                robot_id=ROBOT_ID,
                action_type="PICK_AND_PLACE",
                target_object="sealed frozen-meal tray, lot LOT-PF114-1124-B",
                destination_target="case packer infeed, lane 2",
                commanded_velocity_mps=1.2,
                commanded_force_nm=15.0,
                ref="WO-2026-11-24-0731/segment-0042",
                rush=True,
            ),
            dataclasses.replace(base, nearest_human_m=0.6),
            Outcome.BLOCKED,
            live=True,
        ),
        Row(
            9,
            "Release",
            "Signed order, verified clearance, matched tooling, de-rated speed",
            batch_start(issuers),
            base,
            Outcome.RELEASED,
            live=True,
        ),
    ]
