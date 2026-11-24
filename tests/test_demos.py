"""Demos run end to end offline against the fake server through the real SDK clients."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.fakes import FAKE_API_KEY, FakeRamenServer

ROOT = Path(__file__).resolve().parents[1]


def load(relative: str) -> ModuleType:
    path = ROOT / relative
    name = "demo_" + path.stem
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


kinematic = load("demos/00_quickstart/kinematic_gate_demo.py")
asimov = load("demos/00_quickstart/asimov_robotics.py")


def test_kinematic_local_only(capsys: pytest.CaptureFixture[str]) -> None:
    assert kinematic.main([], environ={}) == 0
    out = capsys.readouterr().out
    assert out.count("LOCAL_BLOCK | LOCAL_BLOCK") == 3 and "skipped" in out


def test_kinematic_trial_uses_two_requests(capsys: pytest.CaptureFixture[str]) -> None:
    server = FakeRamenServer()
    assert kinematic.main(["--trial"], environ={}, client=server.trial_client()) == 0
    out = capsys.readouterr().out
    assert len(server.requests) == 2
    assert all(request.url.path == "/api/v1/paas/try" for request in server.requests)
    assert all("authorization" not in request.headers for request in server.requests)
    assert "Controller segments released: 1 ['ASIMOV-COLLAB-001']" in out
    assert "(trial)" in out


def test_kinematic_keyed(capsys: pytest.CaptureFixture[str]) -> None:
    server = FakeRamenServer()
    environ = {"RAMEN_API_KEY": FAKE_API_KEY}
    assert kinematic.main(["--trial"], environ=environ, client=server.keyed_client()) == 0
    out = capsys.readouterr().out
    assert "--trial ignored" in out and FAKE_API_KEY not in out
    assert {request.url.path for request in server.requests} == {"/api/v1/paas/evaluate"}


def test_kinematic_unexpected_verdict_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    server = FakeRamenServer(decide=lambda _: True)
    assert kinematic.main(["--trial"], environ={}, client=server.trial_client()) == 1
    assert "UNEXPECTED" in capsys.readouterr().err


def test_kinematic_trial_limit_is_a_partial_run(capsys: pytest.CaptureFixture[str]) -> None:
    server = FakeRamenServer(limit_after=0)
    assert kinematic.main(["--trial"], environ={}, client=server.trial_client()) == 0
    out = capsys.readouterr().out
    assert "Partial run" in out and len(server.requests) == 1


def test_asimov_trial_default_is_two_cases() -> None:
    server = FakeRamenServer()
    assert asimov.main(["--trial"], environ={}, client=server.trial_client()) == 0
    assert len(server.requests) == 2


def test_asimov_trial_single_case(capsys: pytest.CaptureFixture[str]) -> None:
    server = FakeRamenServer()
    assert asimov.main(["--trial", "--cases", "4"], environ={}, client=server.trial_client()) == 0
    assert len(server.requests) == 1
    assert "ARM-PACK-05" in server.requests[0].content.decode()
    assert "Blocked-path canary executions: 0" in capsys.readouterr().out


def test_asimov_keyed_runs_all_five(capsys: pytest.CaptureFixture[str]) -> None:
    server = FakeRamenServer()
    assert asimov.main([], environ={"RAMEN_API_KEY": FAKE_API_KEY}, client=server.keyed_client()) == 0
    assert len(server.requests) == 5
    assert "Blocked-path canary executions: 0" in capsys.readouterr().out


def test_asimov_rejects_unknown_cases() -> None:
    with pytest.raises(SystemExit):
        asimov.main(["--cases", "9"], environ={})


def test_payloads_use_application_wording() -> None:
    for scenario in [*asimov.CASES.values(), *kinematic.scenarios()]:
        assert not str(scenario.payload.get("robot_id", "")).upper().startswith("COBOT")
        assert "cobot" not in scenario.label.lower()
