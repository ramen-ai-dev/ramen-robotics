"""Classify and independently re-verify Schema V5 receipts.

Verification is delegated to :func:`ramen_foundry.core.receipts.signed_verdict_problem`
and trial detection to :func:`ramen_ai.is_trial_receipt_id`. This module adds no
cryptography of its own.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal

from ramen_ai import is_trial_receipt_id
from ramen_foundry.core.receipts import (
    VERDICT_ALLOW,
    VERDICT_BLOCK,
    extract_receipt,
    signed_verdict_problem,
)

ReceiptClass = Literal["production", "trial"]

__all__ = [
    "VERDICT_ALLOW",
    "VERDICT_BLOCK",
    "ReceiptClass",
    "evaluated_text",
    "extract_receipt",
    "receipt_class",
    "receipt_problem",
]


def evaluated_text(tool_name: str, arguments: Mapping[str, Any]) -> str:
    """Return the exact text ramen foundry evaluates for one resolved tool call.

    The receipt's ``payload_hash`` is the SHA-256 of this string.
    """
    return json.dumps(
        {"tool": tool_name, "arguments": dict(arguments)},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def receipt_class(receipt: Mapping[str, Any] | None) -> ReceiptClass | None:
    """Return ``"trial"`` for a ``trial_<uuid>`` receipt, ``"production"`` otherwise, ``None`` if absent."""
    if receipt is None:
        return None
    return "trial" if is_trial_receipt_id(receipt.get("id")) else "production"


def receipt_problem(
    receipt: Mapping[str, Any] | None,
    input_text: str,
    *,
    allowed: bool,
    public_keys: Mapping[str, str] | None = None,
) -> str | None:
    """Return why ``receipt`` is not signed evidence of this verdict on ``input_text``, or ``None``.

    ``public_keys=None`` selects the production keys pinned by ramen-ai-core.
    """
    if receipt is None:
        return "no Schema V5 receipt was returned"
    expected = VERDICT_ALLOW if allowed else VERDICT_BLOCK
    problem: str | None = signed_verdict_problem(
        {"receipt": dict(receipt)}, input_text, expected, public_keys
    )
    return problem
