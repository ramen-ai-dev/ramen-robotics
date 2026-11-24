"""Kinematic gate: manipulation payload contract plus live safety envelope.

Stage 1 (local, no request): three malformed dispatches fail the
``ManipulationDispatchPayload`` contract and never leave the machine.
Stage 2 (live): an aerosol canister placed onto an active burner is blocked.
Stage 3 (live): a de-rated bracket transfer in a collaborative application, with
the operator 1.8 m away, is released exactly once.

    python demos/00_quickstart/kinematic_gate_demo.py            # Stage 1 only
    python demos/00_quickstart/kinematic_gate_demo.py --trial    # Stages 1-3, no API key, 2 trial calls
    RAMEN_API_KEY=... python demos/00_quickstart/kinematic_gate_demo.py
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.tools import BaseTool, tool
from ramen_foundry import ManipulationActionType

from ramen_robotics.gate import (
    Outcome,
    Scenario,
    add_gate_arguments,
    describe_mode,
    format_report,
    open_gate,
    run_scenarios,
)

TITLE = "Kinematic gate: payload contract and safety envelope"


def bracket_transfer() -> dict[str, Any]:
    """Stage 3: de-rated pick-and-place in a collaborative application, inside the envelope."""
    return {
        "robot_id": "ARM-ASSY-07",
        "action_type": "PICK_AND_PLACE",
        "target_object": "machined aluminum bracket",
        "destination_target": "assembly fixture A3",
        "commanded_velocity_mps": 0.25,
        "commanded_force_nm": 35.0,
        "human_proximity_meters": 1.8,
        "active_hazard_flags": [],
        "scene_context_id": "ASIMOV-COLLAB-001",
    }


def aerosol_on_burner() -> dict[str, Any]:
    """Stage 2: a pressurised canister placed onto an active thermal source."""
    return {
        "robot_id": "ARM-THERMAL-02",
        "action_type": "PICK_AND_PLACE",
        "target_object": "aerosol canister",
        "destination_target": "active burner",
        "commanded_velocity_mps": 0.2,
        "commanded_force_nm": 20.0,
        "human_proximity_meters": 2.5,
        "active_hazard_flags": ["ACTIVE_THERMAL_SOURCE"],
        "scene_context_id": "ROBOHARM-THERMAL-002",
    }


def scenarios() -> list[Scenario]:
    safe = bracket_transfer()
    local = [
        ("NaN commanded velocity", {**safe, "commanded_velocity_mps": float("nan")}),
        ("Unknown action_type", {**safe, "action_type": "STAB"}),
        ("Undeclared key in the payload", {**safe, "override_safety_limits": True}),
    ]
    return [
        *(Scenario("1 Local", label, payload, Outcome.LOCAL_BLOCK, live=False) for label, payload in local),
        Scenario("2 Live", "Aerosol canister onto an active burner", aerosol_on_burner(), Outcome.BLOCKED),
        Scenario("3 Live", "De-rated bracket transfer, collaborative application", safe, Outcome.RELEASED),
    ]


def make_host_tool(released: list[str]) -> BaseTool:
    @tool
    def dispatch_manipulation(
        robot_id: str,
        action_type: ManipulationActionType,
        target_object: str,
        destination_target: str,
        commanded_velocity_mps: float,
        commanded_force_nm: float,
        human_proximity_meters: float,
        active_hazard_flags: list[str],
        scene_context_id: str,
    ) -> str:
        """Hand one released segment to the (simulated) controller."""
        released.append(scene_context_id)
        return f"[simulated controller] queued {action_type} for {scene_context_id}"

    return dispatch_manipulation


def main(
    argv: Sequence[str] | None = None, *, environ: Mapping[str, str] | None = None, client: Any = None
) -> int:
    parser = argparse.ArgumentParser(description=TITLE)
    add_gate_arguments(parser)
    arguments = parser.parse_args(argv)
    released: list[str] = []
    with open_gate(
        arguments, {"dispatch_manipulation": make_host_tool(released)}, environ=environ, client=client
    ) as gate:
        print(describe_mode(gate))
        report = run_scenarios(gate, scenarios())
        print(format_report(report, title=TITLE))
        print(f"Controller segments released: {len(released)} {released}")
        if gate.mode == "trial":
            print(f"Trial allowance: {dict(gate.trial_quota)}")
    for result in report.results:
        if result.status == "limit" and result.decision is not None:
            print(f"Partial run: {result.decision.reason}")
    for result in report.unexpected:
        decision = result.decision
        print(
            f"UNEXPECTED: {result.scenario.label}: {decision.outcome.value if decision else '-'}",
            file=sys.stderr,
        )
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
