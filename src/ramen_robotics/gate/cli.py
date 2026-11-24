"""Shared command-line wiring for demos and host scripts built on :class:`RoboticsGate`."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from typing import Any

from langchain_core.tools import BaseTool

from .config import resolve_config
from .gate import RoboticsGate

TRIAL_NOTE = "no API key is read or sent; the allowance is about 10 requests per day per network"


def add_gate_arguments(parser: argparse.ArgumentParser) -> None:
    """Add ``--trial`` and ``--audit PATH`` to a demo's argument parser."""
    parser.add_argument(
        "--trial",
        action="store_true",
        help="with no RAMEN_API_KEY, get live verdicts from the keyless public trial",
    )
    parser.add_argument(
        "--audit",
        metavar="PATH",
        default=None,
        help="append one JSON line per dispatch (receipts included) to PATH",
    )


def open_gate(
    arguments: argparse.Namespace,
    tools: Mapping[str, BaseTool],
    *,
    environ: Mapping[str, str] | None = None,
    client: Any = None,
    **options: Any,
) -> RoboticsGate:
    """Build a gate from parsed demo arguments and the process environment."""
    config = resolve_config(trial=bool(getattr(arguments, "trial", False)), environ=environ, **options)
    return RoboticsGate(tools, config=config, audit_path=getattr(arguments, "audit", None), client=client)


def describe_mode(gate: RoboticsGate) -> str:
    """Return a one-line description of how the gate reaches ramen ai. Never includes a key."""
    config = gate.config
    if config.mode == "keyed":
        note = " (--trial ignored because RAMEN_API_KEY is set)" if config.trial_ignored else ""
        return f"Mode: RAMEN_API_KEY | endpoint {config.base_url}{note}"
    if config.mode == "trial":
        return f"Mode: keyless public trial | endpoint {config.base_url}/api/v1/paas/try | {TRIAL_NOTE}"
    return (
        "Mode: local contract checks only. Run with --trial (no key) or set RAMEN_API_KEY for live verdicts."
    )
