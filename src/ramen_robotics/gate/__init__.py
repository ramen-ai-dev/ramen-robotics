"""Thin trust and transport core: RoboticsGate, receipts, hold policy and audit trail."""

from .audit import AUDIT_SCHEMA, AuditTrail, verify_audit_file
from .cli import add_gate_arguments, describe_mode, open_gate
from .config import (
    ALLOW_CUSTOM_BASE_URL_ENV,
    CANONICAL_BASE_URLS,
    DEFAULT_BASE_URL,
    DEFAULT_ENV_FILE,
    GateConfig,
    GateConfigError,
    load_env,
    resolve_base_url,
    resolve_config,
)
from .gate import GateDecision, GateUnavailableError, Outcome, RoboticsGate
from .hold import HoldSignal, ReleaseGuard
from .receipts import evaluated_text, receipt_class, receipt_problem
from .scenarios import Scenario, ScenarioReport, ScenarioResult, format_report, run_scenarios
from .scrub import REDACTED, ScrubbingFilter, scrub

__all__ = [
    "ALLOW_CUSTOM_BASE_URL_ENV",
    "AUDIT_SCHEMA",
    "CANONICAL_BASE_URLS",
    "DEFAULT_BASE_URL",
    "DEFAULT_ENV_FILE",
    "REDACTED",
    "AuditTrail",
    "GateConfig",
    "GateConfigError",
    "GateDecision",
    "GateUnavailableError",
    "HoldSignal",
    "Outcome",
    "ReleaseGuard",
    "RoboticsGate",
    "Scenario",
    "ScenarioReport",
    "ScenarioResult",
    "ScrubbingFilter",
    "add_gate_arguments",
    "describe_mode",
    "evaluated_text",
    "format_report",
    "load_env",
    "open_gate",
    "receipt_class",
    "receipt_problem",
    "resolve_base_url",
    "resolve_config",
    "run_scenarios",
    "scrub",
    "verify_audit_file",
]
