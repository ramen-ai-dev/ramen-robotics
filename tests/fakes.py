"""In-process stand-ins for the ramen ai evaluate and trial routes.

Responses carry genuinely signed Schema V5 receipts made with a generated Ed25519
key. ``tests/conftest.py`` maps the production kid to this key, so every receipt
check in ramen-ai-core, ramen foundry and ramen robotics runs for real. The real
``RamenClient`` and ``TrialComplianceClient`` talk to :class:`FakeRamenServer`
through ``httpx.MockTransport``; no socket is opened.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from langchain_core.tools import BaseTool, tool
from ramen_ai import RamenClient
from ramen_foundry import ManipulationActionType, TrialComplianceClient

SIGNING_KEY = Ed25519PrivateKey.generate()
TEST_PUBLIC_KEYS: dict[str, str] = {
    "ramen_pk_v1": base64.b64encode(
        SIGNING_KEY.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).decode()
}
OTHER_KEY = Ed25519PrivateKey.generate()
FAKE_API_KEY = "rk_test_0123456789abcdef0123456789abcdef"
EXECUTED_AT = "2026-11-24T12:00:00.000Z"
BLOCK_MARKERS = ("active burner", "infant mannequin", "pallet lane B", "energized 480V", "ammonia")


def blocks_on_markers(text: str) -> bool:
    """Default decision: allow unless a known hazardous scenario appears in ``text``."""
    return not any(marker in text for marker in BLOCK_MARKERS)


def signed_receipt(
    text: str,
    *,
    allowed: bool,
    receipt_id: str,
    key: Ed25519PrivateKey = SIGNING_KEY,
    policy_ids: list[str] | None = None,
    signed_verdict: int | None = None,
) -> dict[str, Any]:
    canonical = json.dumps(
        {
            "id": receipt_id,
            "schema_version": "5.0",
            "kid": "ramen_pk_v1",
            "verdict": (1 if allowed else 0) if signed_verdict is None else signed_verdict,
            "payload_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "policy_ids": policy_ids or ["policy-1"],
            "statutory_anchors": [],
            "timestamp": EXECUTED_AT,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    signature = base64.urlsafe_b64encode(key.sign(canonical.encode("utf-8"))).decode().rstrip("=")
    return {"id": receipt_id, "kid": "ramen_pk_v1", "signature": signature, "canonical_payload": canonical}


@dataclass
class FakeRamenServer:
    """Answers ``/api/v1/paas/evaluate`` and ``/api/v1/paas/try``.

    ``tamper`` may rewrite a response's ``data`` before it is returned, to model a
    receipt that does not verify. ``limit_after`` makes trial requests beyond that
    count return HTTP 429.
    """

    decide: Callable[[str], bool] = blocks_on_markers
    tamper: Callable[[dict[str, Any]], None] | None = None
    limit_after: int | None = None
    remaining: int = 9
    fail_with: Exception | None = None
    requests: list[httpx.Request] = field(default_factory=list)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def keyed_client(self, api_key: str = FAKE_API_KEY) -> RamenClient:
        return RamenClient(
            api_key,
            base_url="https://api.ramenai.dev",
            public_keys=TEST_PUBLIC_KEYS,
            transport=self.transport,
        )

    def trial_client(self) -> TrialComplianceClient:
        return TrialComplianceClient(
            base_url="https://api.ramenai.dev", public_keys=TEST_PUBLIC_KEYS, transport=self.transport
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_with is not None:
            raise self.fail_with
        trial = request.url.path == "/api/v1/paas/try"
        trial_count = sum(1 for r in self.requests if r.url.path == "/api/v1/paas/try")
        if trial and self.limit_after is not None and trial_count > self.limit_after:
            return httpx.Response(
                429,
                headers={
                    "X-Ramen-Trial-Scope": "network",
                    "X-Ramen-Trial-Limit": "10",
                    "X-Ramen-Trial-Remaining": "0",
                    "X-Ramen-Trial-Reset": "1893456000",
                },
                json={
                    "success": False,
                    "error": {"code": "TRIAL_LIMIT", "message": "Daily trial limit reached"},
                },
            )
        text = json.loads(request.content)["input"]
        allowed = self.decide(text)
        receipt_id = f"trial_{uuid.uuid4()}" if trial else str(uuid.uuid4())
        data: dict[str, Any] = {
            "allowed": allowed,
            "policy_ids": ["policy-1"],
            "executed_at": EXECUTED_AT,
            "statutory_anchors": [],
            "total_violations": []
            if allowed
            else [{"recovery_instruction": "Hold the arm and route the task to a human."}],
            "results": [],
            "receipt": signed_receipt(text, allowed=allowed, receipt_id=receipt_id),
        }
        if self.tamper is not None:
            self.tamper(data)
        headers = (
            {"X-Ramen-Trial-Limit": "10", "X-Ramen-Trial-Remaining": str(self.remaining)} if trial else {}
        )
        if trial:
            self.remaining = max(0, self.remaining - 1)
        return httpx.Response(200, json={"success": True, "data": data}, headers=headers)


@dataclass
class RecordingController:
    """Host-side controller double: counts released segments."""

    released: list[str] = field(default_factory=list)

    def tool(self, *, fail: bool = False) -> BaseTool:
        released = self.released

        @tool
        def dispatch_manipulation(
            robot_id: str,
            action_type: ManipulationActionType,
            target_object: str,
            destination_target: str,
            commanded_velocity_mps: float,
            commanded_force_nm: float,
            human_proximity_meters: float,
            active_hazard_flags: list[str],
            scene_context_id: str,
        ) -> str:
            """Record one released segment."""
            if fail:
                raise RuntimeError("controller rejected the segment")
            released.append(scene_context_id)
            return f"released {scene_context_id}"

        return dispatch_manipulation


def transfer_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "robot_id": "ARM-ASSY-07",
        "action_type": "PICK_AND_PLACE",
        "target_object": "machined aluminum bracket",
        "destination_target": "assembly fixture A3",
        "commanded_velocity_mps": 0.25,
        "commanded_force_nm": 35.0,
        "human_proximity_meters": 1.8,
        "active_hazard_flags": [],
        "scene_context_id": "ASIMOV-COLLAB-001",
    }
    payload.update(overrides)
    return payload


def burner_payload() -> dict[str, Any]:
    return transfer_payload(
        robot_id="ARM-THERMAL-02",
        target_object="aerosol canister",
        destination_target="active burner",
        commanded_velocity_mps=0.2,
        commanded_force_nm=20.0,
        human_proximity_meters=2.5,
        active_hazard_flags=["ACTIVE_THERMAL_SOURCE"],
        scene_context_id="ROBOHARM-THERMAL-002",
    )
