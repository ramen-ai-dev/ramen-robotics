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
    """Add ``--trial``, ``--audit PATH`` and ``--env-file PATH`` to a demo's argument parser."""
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
    parser.add_argument(
        "--env-file",
        metavar="PATH",
        default=None,
        help="read settings such as RAMEN_API_KEY from this dotenv file (default: ./.env if present); "
        "exported variables still win",
    )


def open_gate(
    arguments: argparse.Namespace,
    tools: Mapping[str, BaseTool],
    *,
    environ: Mapping[str, str] | None = None,
    client: Any = None,
    **options: Any,
) -> RoboticsGate:
    """Build a gate from parsed demo arguments, the process environment and a dotenv file."""
    config = resolve_config(
        trial=bool(getattr(arguments, "trial", False)),
        environ=environ,
        env_file=getattr(arguments, "env_file", None),
        **options,
    )
    return RoboticsGate(tools, config=config, audit_path=getattr(arguments, "audit", None), client=client)


def describe_mode(gate: RoboticsGate) -> str:
    """Return a one-line description of how the gate reaches ramen ai. Never includes a key."""
    config = gate.config
    source = f" | env file {config.env_file}" if config.env_file else ""
    if config.mode == "keyed":
        note = " (--trial ignored because RAMEN_API_KEY is set)" if config.trial_ignored else ""
        return f"Mode: RAMEN_API_KEY | endpoint {config.base_url}{source}{note}"
    if config.mode == "trial":
        return (
            f"Mode: keyless public trial | endpoint {config.base_url}/api/v1/paas/try{source} | {TRIAL_NOTE}"
        )
    return (
        "Mode: local contract checks only. Run with --trial (no key) or set RAMEN_API_KEY "
        f"(shell or .env) for live verdicts.{source}"
    )
