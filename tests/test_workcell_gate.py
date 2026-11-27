"""workcell_gate: pre-checks, composition with RoboticsGate, and the hash-chained journal."""

from __future__ import annotations

import dataclasses
import json
import math
import stat
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from demos.stage2_industrial_packaging import scenario as sc
from ramen_foundry import Ed25519TokenVerifier, manipulation_payload_problem

from ramen_robotics.gate import GateConfig, Outcome, RoboticsGate, evaluated_text
from ramen_robotics.workcell_gate import (
    BatchStart,
    CellMotion,
    CellProfile,
    CellSnapshot,
    EvidenceIssuer,
    FindingCode,
    PlcEnvelope,
    ProfileError,
    RecipeExport,
    SafetyParameterChange,
    SetpointChange,
    SpeedEnvelope,
    StaticStateProvider,
    WorkcellGate,
    WorkcellJournal,
    default_prechecks,
    read_entries,
    run_prechecks,
    to_dispatch,
    verify_chain,
)
from tests.fakes import FAKE_API_KEY, FakeRamenServer, signed_receipt

BLOCK_TERMS = ("SET_DIAGNOSTIC_BYPASS", "OPERATOR_IN_COLLABORATIVE_ZONE")


def workcell_decide(text: str) -> bool:
    return not any(term in text for term in BLOCK_TERMS)


@dataclasses.dataclass
class Bench:
    issuers: sc.Issuers
    profile: CellProfile
    base: CellSnapshot
    server: FakeRamenServer
    provider: StaticStateProvider
    gate: RoboticsGate
    workcell: WorkcellGate
    reached: list[str]
    tmp: Path

    def submit(self, request: Any, snapshot: CellSnapshot | None = None) -> Any:
        self.provider.set(snapshot or self.base)
        return self.workcell.submit(request)


def make_bench(
    tmp_path: Path, *, server: FakeRamenServer | None = None, tools: tuple[str, ...] | None = None
) -> Bench:
    from demos.stage2_industrial_packaging.packaging_cell_demo import make_host_tools

    issuers = sc.Issuers.generate()
    profile = sc.build_profile(issuers)
    base = sc.healthy_snapshot(issuers)
    server = server or FakeRamenServer(decide=workcell_decide)
    reached: list[str] = []
    host = make_host_tools(reached)
    if tools is not None:
        host = {name: host[name] for name in tools}
    gate = RoboticsGate(
        host,
        config=GateConfig(mode="keyed", api_key=FAKE_API_KEY),
        client=server.keyed_client(),
        audit_path=str(tmp_path / "receipts.jsonl"),
    )
    provider = StaticStateProvider(base)
    workcell = WorkcellGate(
        profile,
        gate,
        provider,
        journal_path=tmp_path / "workcell.jsonl",
        receipts_path=tmp_path / "receipts.jsonl",
    )
    return Bench(issuers, profile, base, server, provider, gate, workcell, reached, tmp_path)


@pytest.fixture
def bench(tmp_path: Path) -> Bench:
    return make_bench(tmp_path)


def codes(decision: Any) -> set[FindingCode]:
    return set(decision.codes)


# -- evidence tokens ------------------------------------------------------------------


def test_issuer_tokens_verify_with_ramen_foundry() -> None:
    issuer = EvidenceIssuer("mes-1")
    token = issuer.issue({"a": 1, "exp": time.time() + 60})
    verifier = Ed25519TokenVerifier(issuer.pin())
    assert verifier.verify(token, expected={"a": 1}).valid
    assert not verifier.verify(token, expected={"a": 2}).valid
    assert not Ed25519TokenVerifier(EvidenceIssuer("mes-1").pin()).verify(token, expected={}).valid
    expired = issuer.issue({"a": 1, "exp": time.time() - 1})
    assert not verifier.verify(expired, expected={}).valid
    assert "mes-1" in repr(issuer) and issuer.public_hex not in repr(issuer)


@pytest.mark.parametrize("kid", ["", "a.b"])
def test_issuer_rejects_bad_kid(kid: str) -> None:
    with pytest.raises(ValueError):
        EvidenceIssuer(kid)


# -- profile --------------------------------------------------------------------------


def test_config_hash_is_stable_and_sensitive(bench: Bench) -> None:
    again = sc.build_profile(bench.issuers)
    assert again.config_hash() == bench.profile.config_hash()
    changed = dataclasses.replace(bench.profile, collaborative_zone_m=1.2)
    assert changed.config_hash() != bench.profile.config_hash()
    json.dumps(bench.profile.to_dict(), allow_nan=False)


def test_profile_round_trips_through_dict(bench: Bench) -> None:
    data = json.loads(json.dumps(bench.profile.to_dict()))
    again = CellProfile.from_dict(data)
    assert again.config_hash() == bench.profile.config_hash()
    assert again.plc_envelope(*sc.FREEZER) == bench.profile.plc_envelope(*sc.FREEZER)
    unbounded = dataclasses.replace(bench.profile, plc_envelopes=(PlcEnvelope("N", "T", "C", "E"),))
    assert CellProfile.from_dict(unbounded.to_dict()).plc_envelopes[0].max_slew_per_s == math.inf


@pytest.mark.parametrize(
    "build",
    [
        lambda: CellProfile(cell_id="C", application="a", robot_ids=()),
        lambda: CellProfile(cell_id=" ", application="a", robot_ids=("R",)),
        lambda: CellProfile(
            cell_id="C",
            application="a",
            robot_ids=("R",),
            speed_envelopes=(SpeedEnvelope("automatic", 1, 0), SpeedEnvelope("automatic", 1, 0)),
        ),
        lambda: CellProfile(
            cell_id="C", application="a", robot_ids=("R",), recipe_tooling={"X": frozenset()}
        ),
        lambda: CellProfile(cell_id="C", application="a", robot_ids=("R",), sensor_min_confidence=1.5),
        lambda: SpeedEnvelope("turbo", 1, 0),  # type: ignore[arg-type]
        lambda: SpeedEnvelope("automatic", float("nan"), 0),
        lambda: PlcEnvelope("N", "T", "C", "E", min_val=0, max_val=-1),
        lambda: PlcEnvelope("N", "T", "C", "E", max_slew_per_s=0),
        lambda: PlcEnvelope("N", " ", "C", "E"),
    ],
)
def test_profile_validation(build: Any) -> None:
    with pytest.raises(ProfileError):
        build()


# -- demo rows 1-6: local holds, zero requests ------------------------------------------


@pytest.mark.parametrize("number", [1, 2, 3, 4, 5, 6])
def test_local_rows_hold_without_requests(tmp_path: Path, number: int) -> None:
    bench = make_bench(tmp_path)
    row = next(row for row in sc.build_rows(bench.issuers) if row.number == number)
    decision = bench.submit(row.request, row.snapshot)
    assert decision.outcome is Outcome.LOCAL_BLOCK and decision.stage == "precheck"
    assert row.expected_code in codes(decision)
    assert decision.hold and decision.gate is None
    assert bench.server.requests == [] and bench.reached == []


@pytest.mark.parametrize("number", [1, 2, 3, 4, 5, 6])
def test_rush_never_changes_findings(tmp_path: Path, number: int) -> None:
    bench = make_bench(tmp_path)
    row = next(row for row in sc.build_rows(bench.issuers) if row.number == number)
    calm = dataclasses.replace(row.request, rush=False)
    prechecks = default_prechecks()
    assert run_prechecks(prechecks, row.request, row.snapshot, bench.profile) == run_prechecks(
        prechecks, calm, row.snapshot, bench.profile
    )


def test_rush_is_not_part_of_the_evaluated_payload(bench: Bench) -> None:
    rushed = sc.batch_start(bench.issuers)
    calm = dataclasses.replace(rushed, rush=False)
    assert to_dispatch(rushed, bench.base, bench.profile) == to_dispatch(calm, bench.base, bench.profile)


# -- work order -------------------------------------------------------------------------


def test_work_order_from_unpinned_issuer(bench: Bench) -> None:
    rogue = EvidenceIssuer("mes-1")
    request = dataclasses.replace(
        sc.batch_start(bench.issuers), work_order_token=rogue.issue(sc.work_order())
    )
    assert FindingCode.WORK_ORDER_UNVERIFIED in codes(bench.submit(request))


@pytest.mark.parametrize("token", ["", "garbage", "v1.mes-1.e30.AAAA"])
def test_work_order_malformed(bench: Bench, token: str) -> None:
    request = dataclasses.replace(sc.batch_start(bench.issuers), work_order_token=token)
    assert FindingCode.WORK_ORDER_UNVERIFIED in codes(bench.submit(request))


def test_work_order_expired(bench: Bench) -> None:
    claims = {**sc.work_order(), "exp": time.time() - 5}
    assert FindingCode.WORK_ORDER_UNVERIFIED in codes(
        bench.submit(sc.batch_start(bench.issuers, signed=claims))
    )


def test_work_order_recipe_swapped(bench: Bench) -> None:
    claims = sc.work_order(recipe=sc.PEANUT_RECIPE)
    assert FindingCode.WORK_ORDER_MISMATCH in codes(
        bench.submit(sc.batch_start(bench.issuers, signed=claims))
    )


def test_no_pinned_work_order_issuer(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.workcell.profile = dataclasses.replace(bench.profile, work_order_issuers={})
    assert FindingCode.WORK_ORDER_UNVERIFIED in codes(bench.submit(sc.batch_start(bench.issuers)))


# -- changeover -----------------------------------------------------------------------


def _sanitation(bench: Bench, **claims: Any) -> str:
    base = {
        "cell_id": sc.CELL_ID,
        "previous_recipe_id": sc.PEANUT_RECIPE,
        "next_recipe_id": sc.PEANUT_FREE_RECIPE,
        "swab_result": "PASS",
        "exp": time.time() + 60,
    }
    return bench.issuers.sanitation.issue({**base, **claims})


@pytest.mark.parametrize(
    "claims", [{"swab_result": "FAIL"}, {"next_recipe_id": "R-OTHER"}, {"cell_id": "CELL-X"}, {"exp": 0}]
)
def test_sanitation_token_must_bind_this_changeover(bench: Bench, claims: dict[str, Any]) -> None:
    snapshot = dataclasses.replace(bench.base, sanitation_token=_sanitation(bench, **claims))
    assert FindingCode.SANITATION_UNVERIFIED in codes(bench.submit(sc.batch_start(bench.issuers), snapshot))


def test_sanitation_token_from_mes_issuer_is_rejected(bench: Bench) -> None:
    token = bench.issuers.mes.issue({"cell_id": sc.CELL_ID, "exp": time.time() + 60})
    snapshot = dataclasses.replace(bench.base, sanitation_token=token)
    assert FindingCode.SANITATION_UNVERIFIED in codes(bench.submit(sc.batch_start(bench.issuers), snapshot))


def test_no_clearance_needed_without_allergen_carryover(bench: Bench) -> None:
    snapshot = dataclasses.replace(bench.base, previous_recipe_id=None, sanitation_token=None)
    assert bench.submit(sc.batch_start(bench.issuers), snapshot).outcome is Outcome.RELEASED


def test_recipe_loaded_must_match_order(bench: Bench) -> None:
    snapshot = dataclasses.replace(bench.base, active_recipe_id=sc.PEANUT_RECIPE)
    assert FindingCode.RECIPE_MISMATCH in codes(bench.submit(sc.batch_start(bench.issuers), snapshot))


def test_undeclared_previous_recipe(bench: Bench) -> None:
    snapshot = dataclasses.replace(bench.base, previous_recipe_id="R-UNKNOWN")
    assert FindingCode.ALLERGEN_CONFLICT in codes(bench.submit(sc.batch_start(bench.issuers), snapshot))


def test_undeclared_order_recipe(bench: Bench) -> None:
    claims = sc.work_order(recipe="R-UNKNOWN")
    request = sc.batch_start(bench.issuers, signed=claims, recipe_id="R-UNKNOWN")
    found = codes(bench.submit(request))
    assert FindingCode.ALLERGEN_CONFLICT in found and FindingCode.TOOLING_MISMATCH in found


# -- sensors --------------------------------------------------------------------------


def _sensor(bench: Bench, **changes: Any) -> CellSnapshot:
    sensors = tuple(
        dataclasses.replace(s, **changes) if s.sensor_id == "tray_camera_infeed" else s
        for s in bench.base.sensors
    )
    return dataclasses.replace(bench.base, sensors=sensors)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"age_s": 9.0}, FindingCode.SENSOR_STALE),
        ({"confidence": float("nan")}, FindingCode.SENSOR_DEGRADED),
        ({"confidence": 0.79}, FindingCode.SENSOR_DEGRADED),
        ({"faults": ("LENS_OCCLUDED",)}, FindingCode.SENSOR_DEGRADED),
    ],
)
def test_degraded_sensor_holds(bench: Bench, changes: dict[str, Any], code: FindingCode) -> None:
    decision = bench.submit(sc.batch_start(bench.issuers), _sensor(bench, **changes))
    assert code in codes(decision) and bench.server.requests == []


def test_missing_required_sensor_holds(bench: Bench) -> None:
    snapshot = dataclasses.replace(bench.base, sensors=bench.base.sensors[1:])
    assert FindingCode.SENSOR_STALE in codes(bench.submit(sc.batch_start(bench.issuers), snapshot))


def test_missing_separation_reading_holds(bench: Bench) -> None:
    snapshot = dataclasses.replace(bench.base, nearest_human_m=None)
    assert FindingCode.SENSOR_STALE in codes(bench.submit(sc.batch_start(bench.issuers), snapshot))


def test_sensor_threshold_is_inclusive(bench: Bench) -> None:
    assert (
        bench.submit(sc.batch_start(bench.issuers), _sensor(bench, confidence=0.8)).outcome
        is Outcome.RELEASED
    )


# -- people and safety ------------------------------------------------------------------


def _motion(**changes: Any) -> CellMotion:
    base = CellMotion(
        robot_id=sc.ROBOT_ID,
        action_type="PICK_AND_PLACE",
        target_object="tray",
        destination_target="infeed",
        commanded_velocity_mps=0.3,
        commanded_force_nm=10.0,
        ref="seg-1",
    )
    return dataclasses.replace(base, **changes)


def test_loto_holds_motion_and_setpoints(bench: Bench) -> None:
    snapshot = dataclasses.replace(bench.base, loto_active=True)
    assert FindingCode.LOTO_ACTIVE in codes(bench.submit(_motion(), snapshot))
    setpoint = SetpointChange(*sc.FREEZER, target_val=-20.0, ramp_rate_sec=600.0)
    assert FindingCode.LOTO_ACTIVE in codes(bench.submit(setpoint, snapshot))


def test_safeguard_fault_holds(bench: Bench) -> None:
    snapshot = dataclasses.replace(bench.base, safeguards_ok={"light_curtain_infeed_a": True})
    assert FindingCode.SAFEGUARD_FAULT in codes(bench.submit(_motion(), snapshot))


def test_batch_start_needs_automatic_mode(bench: Bench) -> None:
    snapshot = dataclasses.replace(bench.base, mode="manual_reduced_speed")
    assert FindingCode.MODE_CONFLICT in codes(bench.submit(sc.batch_start(bench.issuers), snapshot))


def test_speed_above_mode_envelope_holds(bench: Bench) -> None:
    snapshot = dataclasses.replace(bench.base, mode="manual_reduced_speed")
    assert FindingCode.MODE_CONFLICT in codes(bench.submit(_motion(commanded_velocity_mps=0.3), snapshot))
    maintenance = dataclasses.replace(bench.base, mode="maintenance")
    assert FindingCode.MODE_CONFLICT in codes(bench.submit(_motion(), maintenance))
    assert FindingCode.MODE_CONFLICT in codes(bench.submit(_motion(commanded_velocity_mps=math.inf)))


@pytest.mark.parametrize("distance", [0.0, 0.49])
def test_separation_below_envelope_floor_holds_locally(bench: Bench, distance: float) -> None:
    decision = bench.submit(_motion(), dataclasses.replace(bench.base, nearest_human_m=distance))
    assert FindingCode.SEPARATION_BELOW_ENVELOPE in codes(decision)
    assert bench.server.requests == []


def test_unknown_robot_holds(bench: Bench) -> None:
    assert FindingCode.ROBOT_UNKNOWN in codes(bench.submit(_motion(robot_id="ARM-OTHER-01")))


def _mute(**changes: Any) -> SafetyParameterChange:
    row = next(row for row in sc.build_rows(sc.Issuers.generate()) if row.number == 7)
    assert isinstance(row.request, SafetyParameterChange)
    return dataclasses.replace(row.request, **changes)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"tag": "OTHER_TAG"}, FindingCode.PLC_UNKNOWN_TAG),
        ({"duration_sec": 0}, FindingCode.SAFETY_REQUEST_INVALID),
        ({"duration_sec": math.inf}, FindingCode.SAFETY_REQUEST_INVALID),
        ({"management_of_change_id": " "}, FindingCode.SAFETY_REQUEST_INVALID),
        ({"physical_key_interlock_verified": True}, FindingCode.KEY_EVIDENCE_UNVERIFIED),
    ],
)
def test_safety_parameter_local_holds(bench: Bench, changes: dict[str, Any], code: FindingCode) -> None:
    decision = bench.submit(_mute(**changes))
    assert code in codes(decision) and bench.server.requests == []


def test_asserted_key_evidence_needs_a_matching_lockout_token(bench: Bench) -> None:
    claims = {
        "cell_id": sc.CELL_ID,
        "node_id": sc.LIGHT_CURTAIN[0],
        "tag": sc.LIGHT_CURTAIN[1],
        "management_of_change_id": "MOC-2026-PACK-0412",
        "exp": time.time() + 60,
    }
    other_moc = bench.issuers.lockout.issue({**claims, "management_of_change_id": "MOC-OTHER"})
    held = bench.submit(_mute(physical_key_interlock_verified=True, key_evidence_token=other_moc))
    assert FindingCode.KEY_EVIDENCE_UNVERIFIED in codes(held)
    good = bench.issuers.lockout.issue(claims)
    decision = bench.submit(_mute(physical_key_interlock_verified=True, key_evidence_token=good))
    assert decision.stage == "gate" and len(bench.server.requests) == 1


# -- PLC envelope ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "ramp", "code"),
    [
        (-17.9, 600.0, FindingCode.PLC_OUT_OF_ENVELOPE),
        (-40.0, 6000.0, FindingCode.PLC_OUT_OF_ENVELOPE),
        (float("nan"), 600.0, FindingCode.PLC_OUT_OF_ENVELOPE),
        (-25.0, 60.0, FindingCode.PLC_SLEW_EXCEEDED),
        (-20.0, 0.0, FindingCode.PLC_SLEW_EXCEEDED),
    ],
)
def test_plc_envelope_holds(bench: Bench, target: float, ramp: float, code: FindingCode) -> None:
    assert code in codes(bench.submit(SetpointChange(*sc.FREEZER, target_val=target, ramp_rate_sec=ramp)))
    assert bench.server.requests == []


def test_plc_bound_is_inclusive_and_reaches_the_bundle(bench: Bench) -> None:
    snapshot = dataclasses.replace(bench.base, plc_values={sc.FREEZER: -20.0})
    decision = bench.submit(SetpointChange(*sc.FREEZER, target_val=-18.0, ramp_rate_sec=600.0), snapshot)
    assert decision.outcome is Outcome.RELEASED
    body = json.loads(bench.server.requests[0].content)["input"]
    assert json.loads(body)["arguments"]["current_val"] == -20.0
    assert bench.reached == ["plc: PLC-FRZ-01/TIC_FRZ_Z2_SP -> -18.0 CELSIUS"]


def test_plc_unknown_tag_and_missing_value(bench: Bench) -> None:
    assert FindingCode.PLC_UNKNOWN_TAG in codes(bench.submit(SetpointChange("PLC-X", "T", -20.0, 600.0)))
    snapshot = dataclasses.replace(bench.base, plc_values={})
    assert FindingCode.SENSOR_STALE in codes(
        bench.submit(SetpointChange(*sc.FREEZER, -20.0, 600.0), snapshot)
    )


# -- recipe export ----------------------------------------------------------------------


def test_allowlisted_export_is_local_only_and_never_released(bench: Bench) -> None:
    decision = bench.submit(RecipeExport(sc.PEANUT_FREE_RECIPE, "MES://plant-07/recipes/", ("formulation",)))
    assert decision.stage == "local_only" and decision.outcome is Outcome.HOLD_UNAVAILABLE
    assert decision.hold and not decision.findings and bench.server.requests == []


@pytest.mark.parametrize(
    "request_",
    [
        RecipeExport(sc.PEANUT_FREE_RECIPE, "https://recipe-share.example.net/upload", ("label",)),
        RecipeExport("R-UNKNOWN", "mes://plant-07/recipes", ("label",)),
    ],
)
def test_denied_exports(bench: Bench, request_: RecipeExport) -> None:
    decision = bench.submit(request_)
    assert decision.outcome is Outcome.LOCAL_BLOCK and FindingCode.EGRESS_DENIED in codes(decision)


# -- composition with RoboticsGate ------------------------------------------------------


@pytest.mark.parametrize(
    ("number", "expected"), [(7, Outcome.BLOCKED), (8, Outcome.BLOCKED), (9, Outcome.RELEASED)]
)
def test_live_rows_go_through_robotics_gate(tmp_path: Path, number: int, expected: Outcome) -> None:
    bench = make_bench(tmp_path)
    row = next(row for row in sc.build_rows(bench.issuers) if row.number == number)
    decision = bench.submit(row.request, row.snapshot)
    assert decision.stage == "gate" and decision.outcome is expected
    assert decision.gate is not None and decision.gate.receipt_verified
    assert len(bench.server.requests) == 1
    assert len(bench.reached) == (1 if expected is Outcome.RELEASED else 0)


def test_dispatch_payloads_meet_the_foundry_contract(bench: Bench) -> None:
    rows = {row.number: row for row in sc.build_rows(bench.issuers)}
    for number in (8, 9):
        tool_name, payload = to_dispatch(rows[number].request, rows[number].snapshot, bench.profile)
        assert tool_name == "dispatch_manipulation"
        assert manipulation_payload_problem(payload) is None
        assert payload["scene_context_id"].startswith(
            f"{sc.CELL_ID}/automatic/cfg-{bench.profile.config_hash()[:12]}/"
        )
    assert (
        "OPERATOR_IN_COLLABORATIVE_ZONE"
        in to_dispatch(rows[8].request, rows[8].snapshot, bench.profile)[1]["active_hazard_flags"]
    )
    assert to_dispatch(rows[9].request, rows[9].snapshot, bench.profile)[1]["active_hazard_flags"] == []
    with pytest.raises(TypeError):
        to_dispatch(rows[6].request, rows[6].snapshot, bench.profile)


def test_trial_bodies_fit_the_limit(bench: Bench) -> None:
    for row in sc.build_rows(bench.issuers):
        if not row.live:
            continue
        tool_name, payload = to_dispatch(row.request, row.snapshot, bench.profile)
        body = json.dumps(
            {
                "input": evaluated_text(tool_name, payload),
                "bundle_ids": ["ramen__industrial_iot_actuation_invariance"],
            },
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        assert len(body) < 2048


def test_unregistered_tool_holds_before_any_request(tmp_path: Path) -> None:
    bench = make_bench(tmp_path, tools=("dispatch_manipulation",))
    decision = bench.submit(_mute())
    assert FindingCode.TOOL_NOT_REGISTERED in codes(decision) and bench.server.requests == []


def test_stale_snapshot_holds(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.workcell.state = StaticStateProvider(
        dataclasses.replace(bench.base, taken_at=time.time() - 30), restamp=False
    )
    assert FindingCode.SNAPSHOT_STALE in codes(bench.workcell.submit(sc.batch_start(bench.issuers)))


def test_failing_state_provider_holds(bench: Bench) -> None:
    class Broken:
        def snapshot(self) -> CellSnapshot:
            raise OSError("PLC unreachable")

    bench.workcell.state = Broken()
    decision = bench.workcell.submit(sc.batch_start(bench.issuers))
    assert decision.outcome is Outcome.LOCAL_BLOCK and FindingCode.PRECHECK_ERROR in codes(decision)
    assert decision.snapshot_digest is None


def test_failing_precheck_holds(bench: Bench) -> None:
    class Exploding:
        name = "exploding"

        def applies_to(self, request: Any) -> bool:
            return True

        def check(self, *args: Any) -> Any:
            raise RuntimeError("boom")

    bench.workcell._prechecks = (Exploding(),)
    assert FindingCode.PRECHECK_ERROR in codes(bench.submit(sc.batch_start(bench.issuers)))


def test_gate_holds_pass_through(tmp_path: Path) -> None:
    failing = FakeRamenServer(fail_with=httpx.ConnectError("refused"))
    assert make_bench(tmp_path / "a", server=failing).submit(
        sc.batch_start(sc.Issuers.generate())
    ).outcome in (
        Outcome.HOLD_UNAVAILABLE,
        Outcome.LOCAL_BLOCK,
    )
    bench = make_bench(tmp_path / "b", server=failing)
    decision = bench.submit(sc.batch_start(bench.issuers))
    assert decision.stage == "gate" and decision.outcome is Outcome.HOLD_UNAVAILABLE and bench.reached == []


def test_allow_with_receipt_for_other_text_holds(tmp_path: Path) -> None:
    def tamper(data: dict[str, Any]) -> None:
        data["receipt"] = signed_receipt("other text", allowed=True, receipt_id=data["receipt"]["id"])

    bench = make_bench(tmp_path, server=FakeRamenServer(decide=workcell_decide, tamper=tamper))
    decision = bench.submit(sc.batch_start(bench.issuers))
    assert decision.outcome is Outcome.HOLD_UNVERIFIED and bench.reached == []


# -- journal --------------------------------------------------------------------------


def test_journal_records_every_decision_in_a_chain(bench: Bench) -> None:
    for row in sc.build_rows(bench.issuers):
        bench.submit(row.request, row.snapshot)
    path = bench.tmp / "workcell.jsonl"
    entries = read_entries(path)
    assert [entry["seq"] for entry in entries] == list(range(1, 10))
    assert verify_chain(entries) == []
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    text = path.read_text()
    assert FAKE_API_KEY not in text and "v1.mes-1." not in text and "v1.sanitation-1." not in text
    assert all(entry["rush"] for entry in entries)
    assert [entry["stage"] for entry in entries][-3:] == ["gate", "gate", "gate"]


def test_journal_tampering_is_detected(bench: Bench) -> None:
    for row in sc.build_rows(bench.issuers)[:4]:
        bench.submit(row.request, row.snapshot)
    entries = read_entries(bench.tmp / "workcell.jsonl")
    edited = [dict(entry) for entry in entries]
    edited[1]["outcome"] = "RELEASED"
    assert any("entry 2" in problem for problem in verify_chain(edited))
    assert verify_chain(entries[:1] + entries[2:])
    assert verify_chain(list(reversed(entries)))


def test_journal_resumes_its_chain(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    first = WorkcellJournal(path)
    first.append({"n": 1})
    second = WorkcellJournal(path)
    entry = second.append({"n": 2})
    assert entry["seq"] == 2
    assert verify_chain(read_entries(path)) == []


def test_journal_rejects_non_json_values(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        WorkcellJournal(tmp_path / "j.jsonl").append({"x": float("nan")})
    assert not (tmp_path / "j.jsonl").exists()


def test_demo_wording_uses_application_terms() -> None:
    issuers = sc.Issuers.generate()
    profile = sc.build_profile(issuers)
    text = json.dumps(profile.to_dict()) + json.dumps([row.step for row in sc.build_rows(issuers)])
    assert "cobot" not in text.lower() and "collaborative robot" not in text.lower()
    assert "collaborative application" in profile.application


def test_requests_are_frozen() -> None:
    request = BatchStart("t", "w", "r", "l", 1, "R", "a", "b", 0.1, 1.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.quantity = 2  # type: ignore[misc]
    assert "work_order_token" not in repr(request)
