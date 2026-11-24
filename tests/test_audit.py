"""JSONL audit trail and offline re-verification."""

from __future__ import annotations

import json
import stat
from pathlib import Path

from ramen_robotics.gate import GateConfig, RoboticsGate, evaluated_text, receipt_class, verify_audit_file
from tests.fakes import (
    FAKE_API_KEY,
    TEST_PUBLIC_KEYS,
    FakeRamenServer,
    RecordingController,
    burner_payload,
    signed_receipt,
    transfer_payload,
)


def _run(tmp_path: Path, mode: str) -> Path:
    audit = tmp_path / "audit.jsonl"
    server, controller = FakeRamenServer(), RecordingController()
    if mode == "trial":
        config, client = GateConfig(mode="trial"), server.trial_client()
    else:
        config, client = GateConfig(mode="keyed", api_key=FAKE_API_KEY), server.keyed_client()
    gate = RoboticsGate(
        {"dispatch_manipulation": controller.tool()}, config=config, client=client, audit_path=str(audit)
    )
    gate.dispatch("dispatch_manipulation", transfer_payload(action_type="STAB"))
    gate.dispatch("dispatch_manipulation", burner_payload())
    gate.dispatch("dispatch_manipulation", transfer_payload())
    return audit


def test_keyed_audit_verifies(tmp_path: Path) -> None:
    audit = _run(tmp_path, "keyed")
    lines = [json.loads(line) for line in audit.read_text().splitlines()]
    assert [line["outcome"] for line in lines] == ["LOCAL_BLOCK", "BLOCKED", "RELEASED"]
    assert [line["evaluated"] for line in lines] == [False, True, True]
    assert all(line["schema"] == "ramen-robotics.audit/1" for line in lines)
    assert verify_audit_file(audit) == []
    assert FAKE_API_KEY not in audit.read_text()
    assert stat.S_IMODE(audit.stat().st_mode) == 0o600


def test_trial_lines_are_not_audit_proof_by_default(tmp_path: Path) -> None:
    audit = _run(tmp_path, "trial")
    problems = verify_audit_file(audit)
    assert len(problems) == 2
    assert all("trial receipt" in problem for problem in problems)
    assert verify_audit_file(audit, allow_trial=True) == []


def test_edited_line_fails_verification(tmp_path: Path) -> None:
    audit = _run(tmp_path, "keyed")
    lines = audit.read_text().splitlines()
    entry = json.loads(lines[1])
    entry["input_text"] = entry["input_text"].replace("active burner", "cold shelf")
    lines[1] = json.dumps(entry)
    audit.write_text("\n".join(lines) + "\n")
    problems = verify_audit_file(audit, public_keys=TEST_PUBLIC_KEYS)
    assert len(problems) == 1 and problems[0].startswith("line 2: ")


def test_flipped_verdict_fails_verification(tmp_path: Path) -> None:
    audit = _run(tmp_path, "keyed")
    lines = audit.read_text().splitlines()
    entry = json.loads(lines[1])
    entry["verdict"] = "ALLOW"
    lines[1] = json.dumps(entry)
    audit.write_text("\n".join(lines) + "\n")
    assert any("line 2" in problem for problem in verify_audit_file(audit))


def test_malformed_lines_are_reported(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    audit.write_text('not json\n[1]\n{"evaluated": true}\n\n{"evaluated": false}\n')
    assert verify_audit_file(audit) == [
        "line 1: not valid JSON",
        "line 2: not a JSON object",
        "line 3: missing verdict or input_text",
    ]


def test_receipt_class_comes_from_the_id() -> None:
    text = evaluated_text("dispatch_manipulation", transfer_payload())
    trial = signed_receipt(text, allowed=True, receipt_id="trial_12345678-1234-1234-1234-123456789abc")
    production = signed_receipt(text, allowed=True, receipt_id="12345678-1234-1234-1234-123456789abc")
    assert receipt_class(trial) == "trial"
    assert receipt_class(production) == "production"
    assert receipt_class(None) is None
