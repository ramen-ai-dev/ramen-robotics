"""Issue evidence tokens in the format :class:`ramen_foundry.Ed25519TokenVerifier` accepts.

Production tokens come from the host's MES, sanitation and lockout systems, which hold
their own keys. This helper serves simulators, test benches and adapter development.
Token format: ``v1.<kid>.<b64url claims>.<b64url Ed25519 signature>``, signed over
``b"ramen-evidence-v1\\n" + claims``. The round trip is covered by tests against the
ramen foundry verifier, so a format change upstream fails the suite.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_DOMAIN = b"ramen-evidence-v1\n"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


class EvidenceIssuer:
    """An Ed25519 issuer identified by ``kid``. The private key never leaves this object."""

    def __init__(self, kid: str, private_key: Ed25519PrivateKey | None = None) -> None:
        if not kid or "." in kid:
            raise ValueError("kid must be non-empty and contain no '.'")
        self.kid = kid
        self._key = private_key if private_key is not None else Ed25519PrivateKey.generate()

    def __repr__(self) -> str:
        return f"EvidenceIssuer(kid={self.kid!r})"

    @property
    def public_hex(self) -> str:
        raw = self._key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return raw.hex()

    def pin(self) -> dict[str, str]:
        """``{kid: public key hex}`` for a :class:`CellProfile` issuer map."""
        return {self.kid: self.public_hex}

    def issue(self, claims: Mapping[str, Any]) -> str:
        body = json.dumps(dict(claims), sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = self._key.sign(_DOMAIN + body)
        return f"v1.{self.kid}.{_b64url(body)}.{_b64url(signature)}"
