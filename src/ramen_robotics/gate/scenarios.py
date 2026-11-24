"""Expected-outcome scenario runs for demos, acceptance checks and host harnesses."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from .gate import GateDecision, Outcome, RoboticsGate

Status = Literal["ok", "unexpected", "skipped", "limit"]


@dataclass(frozen=True)
class Scenario:
    """One dispatch with the outcome it is expected to produce."""

    stage: str
    label: str
    payload: Mapping[str, Any]
    expected: Outcome
    live: bool = True
    tool_name: str = "dispatch_manipulation"
    tool_call_id: str | None = None


@dataclass(frozen=True)
class ScenarioResult:
    scenario: Scenario
    status: Status
    decision: GateDecision | None = None


@dataclass
class ScenarioReport:
    results: list[ScenarioResult] = field(default_factory=list)

    @property
    def unexpected(self) -> list[ScenarioResult]:
        return [result for result in self.results if result.status == "unexpected"]

    @property
    def limit_reached(self) -> bool:
        return any(result.status == "limit" for result in self.results)

    @property
    def exit_code(self) -> int:
        """0 when nothing was unexpected (a used-up trial allowance is a partial run, not a failure)."""
        return 1 if self.unexpected else 0


def run_scenarios(gate: RoboticsGate, scenarios: Iterable[Scenario]) -> ScenarioReport:
    """Dispatch each scenario in order.

    Live scenarios are skipped in local mode. After the trial allowance runs out,
    the remaining live scenarios are skipped so no further requests are spent.
    """
    report = ScenarioReport()
    stop_live = False
    for scenario in scenarios:
        if scenario.live and (gate.mode == "local" or stop_live):
            report.results.append(ScenarioResult(scenario, "skipped"))
            continue
        decision = gate.dispatch(
            scenario.tool_name,
            scenario.payload,
            tool_call_id=scenario.tool_call_id
            or str(scenario.payload.get("scene_context_id") or scenario.label),
        )
        if decision.outcome is Outcome.HOLD_TRIAL_LIMIT:
            stop_live = True
            report.results.append(ScenarioResult(scenario, "limit", decision))
            continue
        status: Status = "ok" if decision.outcome is scenario.expected else "unexpected"
        report.results.append(ScenarioResult(scenario, status, decision))
    return report


def format_report(report: ScenarioReport, *, title: str) -> str:
    """Render a fixed-width table: Stage | Scenario | Expected | Outcome | Receipt | Latency."""
    rows: list[Sequence[str]] = [("Stage", "Scenario", "Expected", "Outcome", "Receipt", "Latency")]
    for result in report.results:
        decision = result.decision
        if decision is None:
            outcome, receipt, latency = "skipped", "-", "-"
        else:
            outcome = decision.outcome.value + ("" if result.status in ("ok", "limit") else "  <- UNEXPECTED")
            receipt = _receipt_cell(decision)
            latency = f"{decision.latency_ms:.0f} ms"
        rows.append(
            (
                result.scenario.stage,
                result.scenario.label,
                result.scenario.expected.value,
                outcome,
                receipt,
                latency,
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines = [title, ""]
    for index, row in enumerate(rows):
        lines.append(" | ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip())
        if index == 0:
            lines.append("-+-".join("-" * width for width in widths))
    return "\n".join(lines)


def _receipt_cell(decision: GateDecision) -> str:
    if decision.kid is None:
        return "none (no request sent)" if decision.outcome is Outcome.LOCAL_BLOCK else "none"
    state = "verified" if decision.receipt_verified else "NOT verified"
    return f"{decision.kid} {state} ({decision.receipt_class})"
