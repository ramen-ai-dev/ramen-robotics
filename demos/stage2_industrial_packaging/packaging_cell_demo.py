"""Stage 2: autonomous food-packaging workcell under rush-order pressure.

A rush order arrives at CELL-PACK-03 (frozen-meal case packing in a collaborative
application). Nine supervisory requests run through ``ramen_robotics.workcell_gate``:
six are held by local workcell checks with no request sent, two are blocked live by the
``ramen__industrial_iot_actuation_invariance`` bundle, and one compliant batch start is
released. Every decision is journaled and can be exported as a tamper-evident dossier.

    python demos/stage2_industrial_packaging/packaging_cell_demo.py                 # rows 1-6, no request
    python demos/stage2_industrial_packaging/packaging_cell_demo.py --trial         # rows 1-9, 3 trial calls
    python demos/stage2_industrial_packaging/packaging_cell_demo.py --trial --dossier out/dossier
    RAMEN_API_KEY=... python demos/stage2_industrial_packaging/packaging_cell_demo.py --dossier out/dossier
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool
from ramen_foundry import ManipulationActionType

from ramen_robotics.gate import Outcome, add_gate_arguments, describe_mode, open_gate
from ramen_robotics.workcell_gate import (
    DossierError,
    StaticStateProvider,
    WorkcellDecision,
    WorkcellGate,
    export_dossier,
    verify_dossier,
)

try:  # repository root on sys.path (tests, editable installs)
    from demos.stage2_industrial_packaging.scenario import Issuers, Row, build_profile, build_rows
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from scenario import Issuers, Row, build_profile, build_rows  # type: ignore[no-redef]

TITLE = "Stage 2: food-packaging workcell under rush-order pressure (CELL-PACK-03)"
DEFAULT_RUN_ROOT = Path("out") / "stage2_packaging"
DEFAULT_DEADLINE_S = 15.0


def make_host_tools(log: list[str]) -> dict[str, BaseTool]:
    """Simulated controller and PLC writers. Each records what reached it."""

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
        """Queue one released segment on the (simulated) cell controller."""
        log.append(f"controller: {action_type} {scene_context_id}")
        return f"[simulated controller] queued {scene_context_id}"

    @tool
    def adjust_plc_setpoint(
        node_id: str,
        tag: str,
        target_val: float,
        current_val: float,
        ramp_rate_sec: float,
        engineering_units: str,
        asset_criticality: str,
        operating_envelope_id: str,
    ) -> str:
        """Write one setpoint ramp to the (simulated) PLC."""
        log.append(f"plc: {node_id}/{tag} -> {target_val} {engineering_units}")
        return "[simulated PLC] ramp written"

    @tool
    def mutate_safety_parameter(
        node_id: str,
        tag: str,
        mutation_type: str,
        requested_value: str | float,
        duration_sec: float,
        physical_key_interlock_verified: bool,
        management_of_change_id: str,
    ) -> str:
        """Write one safety-function parameter on the (simulated) safety controller."""
        log.append(f"safety: {node_id}/{tag} {mutation_type}")
        return "[simulated safety controller] parameter written"

    return {
        "dispatch_manipulation": dispatch_manipulation,
        "adjust_plc_setpoint": adjust_plc_setpoint,
        "mutate_safety_parameter": mutate_safety_parameter,
    }


def parse_rows(value: str) -> tuple[int, ...]:
    try:
        numbers = tuple(int(part) for part in value.split(",") if part.strip())
    except ValueError as error:
        raise argparse.ArgumentTypeError("--scenarios takes comma-separated row numbers, e.g. 7,9") from error
    if not numbers or any(not 1 <= number <= 9 for number in numbers):
        raise argparse.ArgumentTypeError("--scenarios must name rows 1-9")
    return numbers


def parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=TITLE)
    add_gate_arguments(parser)
    parser.add_argument("--scenarios", type=parse_rows, default=None, help="comma-separated rows (1-9)")
    parser.add_argument("--run-dir", type=Path, default=None, help="journal and receipts directory")
    parser.add_argument("--dossier", type=Path, default=None, help="export a dossier into this new directory")
    parser.add_argument(
        "--allow-trial-dossier",
        action="store_true",
        help="export trial receipts, labeled as trial evidence (not persistent audit proof)",
    )
    return parser.parse_args(argv)


def _detail(decision: WorkcellDecision | None) -> str:
    if decision is None:
        return "-"
    if decision.findings:
        return ", ".join(sorted({code.value for code in decision.codes}))
    gate = decision.gate
    if gate is not None and gate.kid:
        return f"{gate.kid} {'verified' if gate.receipt_verified else 'NOT verified'} ({gate.receipt_class})"
    return decision.stage


def _table(rows: list[tuple[Row, WorkcellDecision | None, str]]) -> str:
    cells = [("#", "Area", "Step", "Expected", "Outcome", "Findings / receipt", "Latency")]
    for row, decision, status in rows:
        outcome = "skipped" if decision is None else decision.outcome.value
        if status == "unexpected":
            outcome += "  <- UNEXPECTED"
        latency = f"{decision.gate.latency_ms:.0f} ms" if decision is not None and decision.gate else "-"
        cells.append(
            (str(row.number), row.area, row.step, row.expected.value, outcome, _detail(decision), latency)
        )
    widths = [max(len(line[i]) for line in cells) for i in range(len(cells[0]))]
    out = [TITLE, ""]
    for index, line in enumerate(cells):
        out.append(" | ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=True)).rstrip())
        if index == 0:
            out.append("-+-".join("-" * width for width in widths))
    return "\n".join(out)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    client: Any = None,
    gate_options: Mapping[str, Any] | None = None,
) -> int:
    arguments = parse_arguments(argv)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir: Path = arguments.run_dir or DEFAULT_RUN_ROOT / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    receipts = Path(arguments.audit) if arguments.audit else run_dir / "receipts.jsonl"
    arguments.audit = str(receipts)
    journal = run_dir / "workcell.jsonl"

    issuers = Issuers.generate()
    profile = build_profile(issuers)
    profile_path = run_dir / "profile.json"  # public issuer keys only; lets the run be exported later
    profile_path.write_text(json.dumps(profile.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rows = build_rows(issuers)
    if arguments.scenarios is not None:
        rows = [row for row in rows if row.number in arguments.scenarios]
    reached: list[str] = []
    options = {"deadline_s": DEFAULT_DEADLINE_S, **(gate_options or {})}
    results: list[tuple[Row, WorkcellDecision | None, str]] = []
    with open_gate(arguments, make_host_tools(reached), environ=environ, client=client, **options) as gate:
        print(describe_mode(gate))
        print(f"Cell: {profile.cell_id} | {profile.application} | config {profile.config_hash()[:12]}")
        print(f"Journal: {journal} | receipts: {receipts} | profile: {profile_path}")
        provider = StaticStateProvider(rows[0].snapshot if rows else build_rows(issuers)[0].snapshot)
        workcell = WorkcellGate(profile, gate, provider, journal_path=journal, receipts_path=receipts)
        limit_hit = False
        for row in rows:
            if row.live and (gate.mode == "local" or limit_hit):
                results.append((row, None, "skipped"))
                continue
            provider.set(row.snapshot)
            decision = workcell.submit(row.request)
            if decision.outcome is Outcome.HOLD_TRIAL_LIMIT:
                limit_hit = True
                results.append((row, decision, "limit"))
                continue
            matched = decision.outcome is row.expected and (
                row.expected_code is None or row.expected_code in decision.codes
            )
            results.append((row, decision, "ok" if matched else "unexpected"))
        print(_table(results))
        for row, decision, _ in results:
            if decision is not None and decision.gate is not None and decision.gate.steering:
                print(f"Row {row.number} steering: {decision.gate.steering}")
        print(f"Reached controller, PLC or safety controller: {len(reached)} {reached}")
        if gate.mode == "trial":
            print(f"Trial allowance: {dict(gate.trial_quota)}")

    failures = [f"row {row.number}" for row, _, status in results if status == "unexpected"]
    released = sum(
        1 for _, decision, _ in results if decision is not None and decision.outcome is Outcome.RELEASED
    )
    if len(reached) != released:
        failures.append(f"{len(reached)} host executions for {released} released rows")
    for _row, decision, status in results:
        if status == "limit" and decision is not None:
            print(f"Partial run: {decision.reason}")
    if arguments.dossier is not None:
        failures += _dossier(journal, receipts, arguments.dossier, profile, arguments.allow_trial_dossier)
    for failure in failures:
        print(f"UNEXPECTED: {failure}", file=sys.stderr)
    return 1 if failures else 0


def _dossier(journal: Path, receipts: Path, out: Path, profile: Any, allow_trial: bool) -> list[str]:
    try:
        manifest = export_dossier(journal, receipts, out, profile=profile, allow_trial=allow_trial)
    except DossierError as error:
        trial_only = bool(error.problems) and all("trial receipt" in problem for problem in error.problems)
        print(f"Dossier refused: {'; '.join(error.problems)}")
        if trial_only and not allow_trial:
            print("Re-run with --allow-trial-dossier to export a dossier labeled as trial evidence.")
            return []
        return ["dossier export refused"]
    problems = verify_dossier(out)
    print(
        f"Dossier: {manifest.path} | evidence {manifest.evidence_class} | {manifest.decisions} decisions | "
        f"receipts {manifest.receipts_production} production, {manifest.receipts_trial} trial | "
        f"head {manifest.chain_head[:16]}"
    )
    print(f"verify_dossier: {problems if problems else 'intact'}")
    return [f"dossier: {problem}" for problem in problems]


if __name__ == "__main__":
    sys.exit(main())
