"""Stage 2 demo end to end, offline, through the real SDK clients and a fake server."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from demos.stage2_industrial_packaging import packaging_cell_demo as demo

from ramen_robotics.workcell_gate import CellProfile, DossierError, export_dossier, verify_dossier
from tests.fakes import FAKE_API_KEY, FakeRamenServer
from tests.test_workcell_gate import workcell_decide

NO_SPACING = {"min_interval_s": 0.0}


def run(tmp_path: Path, *argv: str, server: FakeRamenServer | None = None, keyed: bool = False) -> int:
    args = ["--run-dir", str(tmp_path / "run"), *argv]
    if server is None:
        return demo.main(args, environ={})
    if keyed:
        return demo.main(args, environ={"RAMEN_API_KEY": FAKE_API_KEY}, client=server.keyed_client())
    return demo.main(["--trial", *args], environ={}, client=server.trial_client(), gate_options=NO_SPACING)


def test_local_mode_sends_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(tmp_path, "--dossier", str(tmp_path / "d")) == 0
    out = capsys.readouterr().out
    assert out.count("LOCAL_BLOCK | LOCAL_BLOCK") == 6 and out.count("skipped") == 3
    assert "verify_dossier: intact" in out


def test_trial_run_uses_three_calls_and_refuses_the_dossier(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = FakeRamenServer(decide=workcell_decide)
    assert run(tmp_path, "--dossier", str(tmp_path / "d"), server=server) == 0
    out = capsys.readouterr().out
    assert len(server.requests) == 3
    assert all(r.url.path == "/api/v1/paas/try" and "authorization" not in r.headers for r in server.requests)
    assert out.count("ramen_pk_v1 verified (trial)") == 3
    assert "Dossier refused" in out and "--allow-trial-dossier" in out
    assert "Reached controller, PLC or safety controller: 1" in out
    assert not (tmp_path / "d").exists()


def test_trial_dossier_with_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    server = FakeRamenServer(decide=workcell_decide)
    assert run(tmp_path, "--dossier", str(tmp_path / "d"), "--allow-trial-dossier", server=server) == 0
    out = capsys.readouterr().out
    assert "evidence trial" in out and "verify_dossier: intact" in out


def test_keyed_run_and_production_dossier(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    server = FakeRamenServer(decide=workcell_decide)
    assert run(tmp_path, "--trial", "--dossier", str(tmp_path / "d"), server=server, keyed=True) == 0
    out = capsys.readouterr().out
    assert {r.url.path for r in server.requests} == {"/api/v1/paas/evaluate"}
    assert "--trial ignored" in out and "evidence production" in out and "verify_dossier: intact" in out
    assert FAKE_API_KEY not in out


def test_unexpected_verdict_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(tmp_path, server=FakeRamenServer(decide=lambda _: True)) == 1
    err = capsys.readouterr().err
    assert "UNEXPECTED: row 7" in err and "UNEXPECTED: row 8" in err


def test_trial_limit_is_a_partial_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    server = FakeRamenServer(decide=workcell_decide, limit_after=1)
    assert run(tmp_path, server=server) == 0
    out = capsys.readouterr().out
    assert len(server.requests) == 2 and "Partial run" in out and "HOLD_TRIAL_LIMIT" in out


def test_scenario_subset(tmp_path: Path) -> None:
    server = FakeRamenServer(decide=workcell_decide)
    assert run(tmp_path, "--scenarios", "7,9", server=server) == 0
    assert len(server.requests) == 2


@pytest.mark.parametrize("value", ["0", "10", "a"])
def test_bad_scenarios_are_rejected(tmp_path: Path, value: str) -> None:
    with pytest.raises(SystemExit):
        run(tmp_path, "--scenarios", value)


def test_finished_run_can_be_exported_later(tmp_path: Path) -> None:
    server = FakeRamenServer(decide=workcell_decide)
    assert run(tmp_path, "--scenarios", "7,8,9", server=server) == 0
    run_dir = tmp_path / "run"
    profile = CellProfile.from_dict(json.loads((run_dir / "profile.json").read_text()))
    journal, receipts = run_dir / "workcell.jsonl", run_dir / "receipts.jsonl"
    with pytest.raises(DossierError):
        export_dossier(journal, receipts, tmp_path / "refused", profile=profile)
    manifest = export_dossier(journal, receipts, tmp_path / "d", profile=profile, allow_trial=True)
    assert manifest.evidence_class == "trial" and manifest.receipts_trial == 3
    assert verify_dossier(tmp_path / "d") == []
    assert "private" not in (run_dir / "profile.json").read_text().lower()
