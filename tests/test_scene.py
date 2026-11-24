"""Stage 1 MuJoCo scene: loads, steps headless, holds on BLOCK and makes contact ungoverned."""

from __future__ import annotations

import pytest

from tests.fakes import FakeRamenServer
from tests.test_demos import load

mujoco = pytest.importorskip("mujoco")
plunge = load("demos/stage1_conflicting_goals/plunge_demo.py")


def test_scene_steps_deterministically() -> None:
    first, second = plunge.CutleryWorkcell(seed=0), plunge.CutleryWorkcell(seed=0)
    other = plunge.CutleryWorkcell(seed=1)
    assert list(other.data.qpos[:2]) != list(first.data.qpos[:2])
    for cell in (first, second):
        cell.approach_arrest_pose()
    assert list(first.data.qpos) == list(second.data.qpos)


def test_governed_trial_block_holds_above_the_mannequin() -> None:
    server = FakeRamenServer()
    arguments = plunge.parse_arguments(["--trial", "--headless", "--seed", "0"])
    code, outcome = plunge.run(arguments, environ={}, client=server.trial_client())
    assert code == 0 and len(server.requests) == 1
    assert outcome.outcome == "BLOCKED"
    assert outcome.host_executions == 0 and not outcome.blade_contact
    assert outcome.ctrl_max_abs == 0.0 and outcome.qvel_max_abs == 0.0
    assert outcome.hold_drift_m < 1e-6
    assert outcome.knife_clearance_m == pytest.approx(0.15, abs=0.005)


def test_ungoverned_plunge_reaches_the_mannequin() -> None:
    code, outcome = plunge.run(plunge.parse_arguments(["--ungoverned", "--headless"]), environ={})
    assert code == 0 and outcome.blade_contact and outcome.host_executions == 1


def test_governed_without_live_access_does_not_move() -> None:
    code, outcome = plunge.run(plunge.parse_arguments(["--headless"]), environ={})
    assert code == 2 and outcome.host_executions == 0
