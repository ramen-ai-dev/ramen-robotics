"""Remove credentials from text before it reaches logs, errors or audit records."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

REDACTED = "[redacted]"

# (pattern, replacement); group 1 keeps the field label so the redaction stays readable.
_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(authorization\s*[:=]\s*)(?:bearer\s+|basic\s+)?[^\s,;'\"]+"), rf"\g<1>{REDACTED}"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), REDACTED),
    (
        re.compile(r"(?i)\b((?:x-)?api[_-]?key|provider[_-]?key|ramen_api_key)(\s*[:=]\s*)['\"]?[^\s,;'\"]+"),
        rf"\g<1>\g<2>{REDACTED}",
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), REDACTED),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), REDACTED),
)

_MIN_SECRET_LENGTH = 4


def scrub(text: object, secrets: Iterable[str | None] = ()) -> str:
    """Return ``text`` as a string with known secrets and credential patterns redacted."""
    result = str(text)
    for secret in sorted({s for s in secrets if s and len(s) >= _MIN_SECRET_LENGTH}, key=len, reverse=True):
        result = result.replace(secret, REDACTED)
    for pattern, replacement in _RULES:
        result = pattern.sub(replacement, result)
    return result


class ScrubbingFilter(logging.Filter):
    """Logging filter that scrubs the rendered message of every record it sees."""

    def __init__(self, secrets: Iterable[str | None] = ()) -> None:
        super().__init__()
        self._secrets = tuple(s for s in secrets if s)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = scrub(record.getMessage(), self._secrets)
        record.args = None
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = scrub(logging.Formatter().formatException(record.exc_info), self._secrets)
            record.exc_info = None
        return True
