"""Credential scrubbing."""

from __future__ import annotations

import logging

import pytest

from ramen_robotics.gate import REDACTED, ScrubbingFilter, scrub


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer abcdefghijklmnop",
        "authorization=abcdefghijklmnop",
        "got bearer abcdefghijklmnop back",
        "X-Provider-Key: " + "sk-" + "abcdefghijklmnopqrstuv",
        "api_key='abcdefghijklmnop'",
        "RAMEN_API_KEY=abcdefghijklmnop",
        "AI" + "zaSyA0123456789abcdefghijklmnopqrstu",
    ],
)
def test_patterns_are_redacted(text: str) -> None:
    result = scrub(text)
    assert "abcdefghijklmnop" not in result and "0123456789abcdef" not in result
    assert REDACTED in result


def test_explicit_secret_is_redacted_anywhere() -> None:
    assert scrub("prefix-supersecretvalue-suffix", ["supersecretvalue"]) == f"prefix-{REDACTED}-suffix"


def test_plain_text_is_unchanged() -> None:
    text = "Hold the arm: commanded velocity 0.85 m/s at 0.45 m separation."
    assert scrub(text, ["", None]) == text


def test_logging_filter_scrubs_messages_and_exceptions(caplog: pytest.LogCaptureFixture) -> None:
    log = logging.getLogger("ramen_robotics.test_scrub")
    log.addFilter(ScrubbingFilter(["supersecretvalue"]))
    caplog.set_level(logging.INFO, logger=log.name)
    try:
        raise RuntimeError("boom supersecretvalue")
    except RuntimeError:
        log.exception("failed with %s", "supersecretvalue")
    assert "supersecretvalue" not in caplog.text
    assert REDACTED in caplog.text
