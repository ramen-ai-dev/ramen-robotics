"""Signed evidence checks through ramen foundry's :class:`Ed25519TokenVerifier`."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from ramen_foundry import Ed25519TokenVerifier

MISMATCH_MARKER = "does not match the request"


def verify_token(
    token: str | None,
    issuers: Mapping[str, str],
    expected: Mapping[str, Any],
    *,
    clock: Callable[[], float] = time.time,
) -> tuple[bool, str | None]:
    """Return ``(valid, reason)``. ``reason`` contains :data:`MISMATCH_MARKER` for a claim mismatch."""
    if not issuers:
        return False, "no issuer keys are pinned in the cell profile"
    verifier = Ed25519TokenVerifier(dict(issuers), clock=clock)
    result = verifier.verify(token, expected=dict(expected))
    return bool(result.valid), (None if result.valid else str(result.reason))
