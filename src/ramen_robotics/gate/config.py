"""Resolve how a :class:`~ramen_robotics.gate.RoboticsGate` reaches ramen ai.

Settings come from the process environment and, optionally, a dotenv file.
The process environment always wins over the file, so an exported or inline
variable overrides a stale ``.env``. When the caller does not pass ``environ``,
``.env`` in the current working directory is read if it exists; ``env_file``
names a different file, which must then exist. Passing ``environ`` without
``env_file`` reads no file, which keeps tests and embedding hosts hermetic.
The file is only consulted here: ``os.environ`` is never modified.

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
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import dotenv_values

GateMode = Literal["keyed", "trial", "local"]

DEFAULT_BASE_URL = "https://api.ramenai.dev"
CANONICAL_BASE_URLS: tuple[str, ...] = (DEFAULT_BASE_URL, "https://eu.ramenai.dev")
API_KEY_ENV = "RAMEN_API_KEY"
BASE_URL_ENV = "RAMEN_BASE_URL"
ALLOW_CUSTOM_BASE_URL_ENV = "RAMEN_ALLOW_CUSTOM_BASE_URL"
DEFAULT_ENV_FILE = ".env"  # relative to the current working directory
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
    env_file: str | None = None  # the dotenv file that was read, if any

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


def load_env(
    environ: Mapping[str, str] | None = None, env_file: str | Path | None = None
) -> tuple[dict[str, str], str | None]:
    """Merge a dotenv file under the environment; return the merged view and the file read.

    See the module docstring for which file is read. Keys with no value in the file
    are ignored. Raises :class:`GateConfigError` if an explicit ``env_file`` is missing.
    """
    base = os.environ if environ is None else environ
    if env_file is not None:
        path = Path(env_file)
        if not path.is_file():
            raise GateConfigError(f"env file not found: {path}")
    elif environ is None:
        path = Path(DEFAULT_ENV_FILE)
        if not path.is_file():
            return dict(base), None
    else:
        return dict(base), None
    values = {key: value for key, value in dotenv_values(path).items() if value is not None}
    return {**values, **base}, str(path)


def resolve_config(
    *,
    trial: bool = False,
    environ: Mapping[str, str] | None = None,
    env_file: str | Path | None = None,
    base_url: str | None = None,
    allow_custom_base_url: bool | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    deadline_s: float | None = None,
    min_interval_s: float | None = None,
) -> GateConfig:
    """Resolve a :class:`GateConfig` from arguments, the environment and a dotenv file."""
    env, loaded = load_env(environ, env_file)
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
            env_file=loaded,
        )
    mode: GateMode = "trial" if trial else "local"
    default_interval = TRIAL_MIN_INTERVAL_S if trial else 0.0
    return GateConfig(
        mode=mode,
        base_url=url,
        timeout_s=timeout_s,
        deadline_s=deadline_s,
        min_interval_s=default_interval if min_interval_s is None else min_interval_s,
        env_file=loaded,
    )
