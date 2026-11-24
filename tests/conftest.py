"""Shared pytest configuration: offline by default, real receipt checks with a test key."""

from __future__ import annotations

import socket

import pytest
import ramen_foundry.core.receipts as foundry_receipts

from tests.fakes import TEST_PUBLIC_KEYS

_GATE_ENV = ("RAMEN_API_KEY", "RAMEN_BASE_URL", "RAMEN_ALLOW_CUSTOM_BASE_URL", "RAMEN_SHADOW_MODE")


@pytest.fixture(autouse=True)
def _no_network_outside_live_tests(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse every socket connection in offline tests.

    No default test may reach api.ramenai.dev, including the keyless trial route,
    whose daily allowance is small and shared per network. ``live`` tests opt out.
    """
    if request.node.get_closest_marker("live"):
        return

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("an offline test attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


@pytest.fixture(autouse=True)
def _verify_with_test_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point ramen foundry's default receipt key map at the test signing key.

    ``IndustrialAutomationAgent`` does not take ``public_keys``, so its ALLOW check
    reads this module global. Fakes sign with the matching private key.
    """
    monkeypatch.setattr(foundry_receipts, "AUDIT_PUBLIC_KEYS", TEST_PUBLIC_KEYS)


@pytest.fixture(autouse=True)
def _isolate_gate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's credentials and endpoint overrides out of every test."""
    for name in _GATE_ENV:
        monkeypatch.delenv(name, raising=False)
