"""RoboticsGate outcomes over the real SDK clients and a fake server."""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx
import pytest

from ramen_robotics.gate import GateConfig, Outcome, RoboticsGate, evaluated_text
from tests.fakes import (
    FAKE_API_KEY,
    OTHER_KEY,
    FakeRamenServer,
    RecordingController,
    burner_payload,
    signed_receipt,
    transfer_payload,
)

KEYED = GateConfig(mode="keyed", api_key=FAKE_API_KEY)
TRIAL = GateConfig(mode="trial")


def _gate(
    server: FakeRamenServer, controller: RecordingController, config: GateConfig = KEYED, **kw: Any
) -> RoboticsGate:
    client = server.trial_client() if config.mode == "trial" else server.keyed_client()
    return RoboticsGate({"dispatch_manipulation": controller.tool()}, config=config, client=client, **kw)


@pytest.mark.parametrize("config", [KEYED, TRIAL], ids=["keyed", "trial"])
def test_verified_allow_releases_once(config: GateConfig) -> None:
    server, controller = FakeRamenServer(), RecordingController()
    gate = _gate(server, controller, config, sleep=lambda _: None)
    decision = gate.dispatch("dispatch_manipulation", transfer_payload(), tool_call_id="t1")
    assert decision.outcome is Outcome.RELEASED
    assert decision.executed and not decision.hold
    assert controller.released == ["ASIMOV-COLLAB-001"]
    assert decision.verdict == "ALLOW" and decision.receipt_verified
    assert decision.kid == "ramen_pk_v1"
    assert decision.receipt_class == ("trial" if config.mode == "trial" else "production")
    assert decision.tool_output == "released ASIMOV-COLLAB-001"


@pytest.mark.parametrize("config", [KEYED, TRIAL], ids=["keyed", "trial"])
def test_verified_block_never_releases(config: GateConfig) -> None:
    server, controller = FakeRamenServer(), RecordingController()
    decision = _gate(server, controller, config).dispatch("dispatch_manipulation", burner_payload())
    assert decision.outcome is Outcome.BLOCKED
    assert not decision.executed and decision.hold
    assert controller.released == []
    assert decision.receipt_verified and decision.verdict == "BLOCK"
    assert decision.steering == "Hold the arm and route the task to a human."
    assert decision.reason == decision.steering


@pytest.mark.parametrize(
    "mutation",
    [{"commanded_velocity_mps": float("nan")}, {"action_type": "STAB"}, {"override_safety_limits": True}],
    ids=["nan", "action_type", "extra_key"],
)
def test_local_contract_violation_sends_nothing(mutation: dict[str, Any]) -> None:
    server, controller = FakeRamenServer(), RecordingController()
    decision = _gate(server, controller).dispatch("dispatch_manipulation", transfer_payload(**mutation))
    assert decision.outcome is Outcome.LOCAL_BLOCK
    assert "ManipulationDispatchPayload" in (decision.reason or "")
    assert server.requests == [] and controller.released == []


def test_allow_with_signature_from_another_key_holds() -> None:
    def tamper(data: dict[str, Any]) -> None:
        canonical = data["receipt"]["canonical_payload"].encode("utf-8")
        data["receipt"]["signature"] = base64.urlsafe_b64encode(OTHER_KEY.sign(canonical)).decode()

    server, controller = FakeRamenServer(tamper=tamper), RecordingController()
    decision = _gate(server, controller).dispatch("dispatch_manipulation", transfer_payload())
    assert decision.outcome is Outcome.HOLD_UNVERIFIED
    assert controller.released == [] and not decision.receipt_verified


def test_allow_carrying_a_signed_block_receipt_holds() -> None:
    def tamper(data: dict[str, Any]) -> None:
        text = evaluated_text("dispatch_manipulation", transfer_payload())
        data["receipt"] = signed_receipt(text, allowed=False, receipt_id=data["receipt"]["id"])

    server, controller = FakeRamenServer(tamper=tamper), RecordingController()
    decision = _gate(server, controller).dispatch("dispatch_manipulation", transfer_payload())
    assert decision.outcome is Outcome.HOLD_UNVERIFIED
    assert controller.released == []


def test_release_check_is_independent_of_the_client_flag() -> None:
    """A client that claims a verified ALLOW for a receipt bound to other input is refused."""
    text = evaluated_text("dispatch_manipulation", burner_payload())

    class LyingClient:
        def evaluate_compliance(self, input_text: str, **_: Any) -> dict[str, Any]:
            receipt = signed_receipt(text, allowed=True, receipt_id="r-1")
            return {"allowed": True, "receipt_verified": True, "data": {"receipt": receipt}}

    controller = RecordingController()
    gate = RoboticsGate({"dispatch_manipulation": controller.tool()}, config=KEYED, client=LyingClient())
    decision = gate.dispatch("dispatch_manipulation", transfer_payload())
    assert decision.outcome is Outcome.HOLD_UNVERIFIED and controller.released == []


def test_deadline_passed_before_release_holds() -> None:
    now = [0.0]

    def slow_allow(_: str) -> bool:
        now[0] += 5.0  # the verdict takes 5 s to arrive
        return True

    server, controller = FakeRamenServer(decide=slow_allow), RecordingController()
    config = GateConfig(mode="keyed", api_key=FAKE_API_KEY, deadline_s=1.0)
    gate = _gate(server, controller, config, clock=lambda: now[0])
    decision = gate.dispatch("dispatch_manipulation", transfer_payload())
    assert decision.outcome is Outcome.HOLD_TIMEOUT
    assert controller.released == []
    assert decision.verdict == "ALLOW" and decision.receipt_verified


def test_transport_failure_holds_and_scrubs_the_key() -> None:
    error = httpx.ConnectError(f"connection refused; Authorization: Bearer {FAKE_API_KEY}")
    server, controller = FakeRamenServer(fail_with=error), RecordingController()
    decision = _gate(server, controller).dispatch("dispatch_manipulation", transfer_payload())
    assert decision.outcome is Outcome.HOLD_UNAVAILABLE
    assert FAKE_API_KEY not in (decision.reason or "")
    assert "[redacted]" in (decision.reason or "")
    assert controller.released == []


def test_trial_limit_holds_with_reset_description() -> None:
    server, controller = FakeRamenServer(limit_after=0), RecordingController()
    decision = _gate(server, controller, TRIAL).dispatch("dispatch_manipulation", transfer_payload())
    assert decision.outcome is Outcome.HOLD_TRIAL_LIMIT
    assert "resets at" in (decision.reason or "")
    assert controller.released == []


def test_host_tool_error_after_release_is_reported() -> None:
    server, controller = FakeRamenServer(), RecordingController()
    gate = RoboticsGate(
        {"dispatch_manipulation": controller.tool(fail=True)}, config=KEYED, client=server.keyed_client()
    )
    decision = gate.dispatch("dispatch_manipulation", transfer_payload())
    assert decision.outcome is Outcome.HOLD_TOOL_ERROR and not decision.executed


def test_local_mode_holds_live_dispatches() -> None:
    controller = RecordingController()
    gate = RoboticsGate({"dispatch_manipulation": controller.tool()}, config=GateConfig(mode="local"))
    decision = gate.dispatch("dispatch_manipulation", transfer_payload())
    assert decision.outcome is Outcome.HOLD_UNAVAILABLE
    assert "--trial" in (decision.reason or "")
    local = gate.dispatch("dispatch_manipulation", transfer_payload(action_type="STAB"))
    assert local.outcome is Outcome.LOCAL_BLOCK


def test_trial_calls_are_spaced() -> None:
    now = [100.0]
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    server, controller = FakeRamenServer(), RecordingController()
    config = GateConfig(mode="trial", min_interval_s=2.0)
    gate = _gate(server, controller, config, clock=lambda: now[0], sleep=sleep)
    gate.dispatch("dispatch_manipulation", burner_payload())
    gate.dispatch("dispatch_manipulation", transfer_payload(action_type="STAB"))  # local, not paced
    gate.dispatch("dispatch_manipulation", transfer_payload())
    assert slept == [2.0]
    assert gate.trial_quota.get("remaining") == 8


def test_repr_and_logs_never_contain_the_key(caplog: pytest.LogCaptureFixture) -> None:
    server, controller = FakeRamenServer(), RecordingController()
    gate = _gate(server, controller)
    caplog.set_level(logging.DEBUG)
    gate.dispatch("dispatch_manipulation", transfer_payload(), tool_call_id=f"id-{FAKE_API_KEY}")
    assert FAKE_API_KEY not in repr(gate)
    assert FAKE_API_KEY not in repr(gate.config)
    assert FAKE_API_KEY not in caplog.text
    assert "outcome=RELEASED" in caplog.text


def test_registry_rules_come_from_ramen_foundry() -> None:
    controller = RecordingController()
    with pytest.raises(ValueError):
        RoboticsGate({"launch_rocket": controller.tool()}, config=GateConfig(mode="local"))


def test_from_env_builds_keyed_trial_and_local(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = {"dispatch_manipulation": RecordingController().tool()}
    with RoboticsGate.from_env(tools, environ={}) as gate:
        assert gate.mode == "local"
    with RoboticsGate.from_env(tools, trial=True, environ={}) as gate:
        assert gate.mode == "trial" and gate.config.min_interval_s == 2.0
    with RoboticsGate.from_env(tools, trial=True, environ={"RAMEN_API_KEY": FAKE_API_KEY}) as gate:
        assert gate.mode == "keyed" and gate.config.trial_ignored
