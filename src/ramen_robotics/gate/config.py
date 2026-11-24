"""Resolve how a :class:`~ramen_robotics.gate.RoboticsGate` reaches ramen ai.

Resolution order:

1. ``RAMEN_API_KEY`` in the environment selects keyed mode. It always wins; a
   ``--trial`` request is then ignored and reported as such.
2. Otherwise ``trial=True`` selects the keyless public trial.
3. Otherwise the gate runs in local mode: payload contract checks run, and every
   dispatch that needs a live verdict holds.

``RAMEN_BASE_URL`` may name ``https://api.ramenai.dev`` or ``https://eu.ramenai.dev``.
Any other ``https://`` host is accepted only with an explicit opt-in, either the
``allow_custom_base_url`` argument or ``RAMEN_ALLOW_CUSTOM_BASE_URL=1``. Receipt
verification keys never change with the base URL.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

GateMode = Literal["keyed", "trial", "local"]

DEFAULT_BASE_URL = "https://api.ramenai.dev"
CANONICAL_BASE_URLS: tuple[str, ...] = (DEFAULT_BASE_URL, "https://eu.ramenai.dev")
API_KEY_ENV = "RAMEN_API_KEY"
BASE_URL_ENV = "RAMEN_BASE_URL"
ALLOW_CUSTOM_BASE_URL_ENV = "RAMEN_ALLOW_CUSTOM_BASE_URL"
DEFAULT_TIMEOUT_S = 10.0
TRIAL_MIN_INTERVAL_S = 2.0

_TRUE = frozenset({"1", "true", "yes", "on"})


class GateConfigError(ValueError):
    """The gate configuration is invalid. Raised before any request is made."""


@dataclass(frozen=True)
class GateConfig:
    """Resolved gate settings. ``api_key`` is excluded from ``repr``."""

    mode: GateMode
    base_url: str = DEFAULT_BASE_URL
    api_key: str | None = field(default=None, repr=False)
    timeout_s: float = DEFAULT_TIMEOUT_S
    deadline_s: float | None = None
    min_interval_s: float = 0.0
    trial_ignored: bool = False

    def __post_init__(self) -> None:
        if self.mode not in ("keyed", "trial", "local"):
            raise GateConfigError(f"unknown gate mode: {self.mode!r}")
        if self.mode == "keyed" and not self.api_key:
            raise GateConfigError("keyed mode needs a non-empty API key")
        if self.mode != "keyed" and self.api_key is not None:
            raise GateConfigError(f"{self.mode} mode never carries an API key")
        _require_positive("timeout_s", self.timeout_s)
        if self.deadline_s is not None:
            _require_positive("deadline_s", self.deadline_s)
        if not _finite(self.min_interval_s) or self.min_interval_s < 0:
            raise GateConfigError("min_interval_s must be a finite number >= 0")


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _require_positive(name: str, value: object) -> None:
    if not _finite(value) or float(value) <= 0:  # type: ignore[arg-type]
        raise GateConfigError(f"{name} must be a finite number > 0")


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUE


def resolve_base_url(value: str | None, *, allow_custom: bool = False) -> str:
    """Return a normalised base URL or raise :class:`GateConfigError`.

    The URL must be ``https`` with a host and no credentials, query, fragment or
    path. Hosts other than the canonical ramen ai hosts need ``allow_custom``.
    """
    if value is None or not value.strip():
        return DEFAULT_BASE_URL
    candidate = value.strip().rstrip("/")
    parts = urlsplit(candidate)
    if parts.scheme != "https":
        raise GateConfigError("RAMEN_BASE_URL must use https://")
    if not parts.hostname:
        raise GateConfigError("RAMEN_BASE_URL must name a host")
    if parts.username is not None or parts.password is not None:
        raise GateConfigError("RAMEN_BASE_URL must not embed credentials")
    if parts.query or parts.fragment or parts.path not in ("", "/"):
        raise GateConfigError("RAMEN_BASE_URL must be a bare origin such as https://api.ramenai.dev")
    normalised = f"https://{parts.netloc.lower()}"
    if normalised not in CANONICAL_BASE_URLS and not allow_custom:
        raise GateConfigError(
            f"RAMEN_BASE_URL {normalised} is not a canonical ramen ai host "
            f"({', '.join(CANONICAL_BASE_URLS)}); set {ALLOW_CUSTOM_BASE_URL_ENV}=1 to opt in"
        )
    return normalised


def resolve_config(
    *,
    trial: bool = False,
    environ: Mapping[str, str] | None = None,
    base_url: str | None = None,
    allow_custom_base_url: bool | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    deadline_s: float | None = None,
    min_interval_s: float | None = None,
) -> GateConfig:
    """Resolve a :class:`GateConfig` from arguments and the environment.

    Only the process environment is read. No dotenv file is loaded.
    """
    env = os.environ if environ is None else environ
    allow_custom = (
        _truthy(env.get(ALLOW_CUSTOM_BASE_URL_ENV))
        if allow_custom_base_url is None
        else allow_custom_base_url
    )
    url = resolve_base_url(
        base_url if base_url is not None else env.get(BASE_URL_ENV), allow_custom=allow_custom
    )
    api_key = (env.get(API_KEY_ENV) or "").strip() or None
    if api_key is not None:
        return GateConfig(
            mode="keyed",
            base_url=url,
            api_key=api_key,
            timeout_s=timeout_s,
            deadline_s=deadline_s,
            min_interval_s=0.0 if min_interval_s is None else min_interval_s,
            trial_ignored=trial,
        )
    mode: GateMode = "trial" if trial else "local"
    default_interval = TRIAL_MIN_INTERVAL_S if trial else 0.0
    return GateConfig(
        mode=mode,
        base_url=url,
        timeout_s=timeout_s,
        deadline_s=deadline_s,
        min_interval_s=default_interval if min_interval_s is None else min_interval_s,
    )
