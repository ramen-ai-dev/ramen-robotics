"""RoboticsGate: one governed entry point from a planner to a robot controller.

The gate composes ramen foundry's :class:`~ramen_foundry.IndustrialAutomationAgent`
(local ``ManipulationDispatchPayload`` checks, then a live verdict from the
``ramen__industrial_iot_actuation_invariance`` bundle) with:

* one client interface for the keyless trial, a paid ``RAMEN_API_KEY`` and a
  configurable base URL;
* an independent Schema V5 Ed25519 receipt check before every release;
* a deterministic hold policy: every outcome other than ``RELEASED`` means the
  host tool did not run and the controller must hold;
* key-scrubbed reasons and logs, and an optional JSONL audit trail.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, cast

from langchain_core.tools import BaseTool
from ramen_ai import RamenClient, TrialLimitError
from ramen_foundry import IndustrialAutomationAgent, TrialComplianceClient, manipulation_payload_problem
from ramen_foundry.trial import describe_trial_limit

from .audit import AuditTrail
from .config import GateConfig, GateConfigError, resolve_config
from .hold import HoldSignal, ReleaseGuard
from .receipts import ReceiptClass, extract_receipt, receipt_class, receipt_problem
from .scrub import scrub

logger = logging.getLogger("ramen_robotics.gate")

MANIPULATION_TOOL_NAME = "dispatch_manipulation"


class Outcome(str, Enum):
    """What happened to one dispatch. Only ``RELEASED`` means the host tool ran."""

    RELEASED = "RELEASED"
    BLOCKED = "BLOCKED"
    LOCAL_BLOCK = "LOCAL_BLOCK"
    HOLD_UNAVAILABLE = "HOLD_UNAVAILABLE"
    HOLD_TIMEOUT = "HOLD_TIMEOUT"
    HOLD_UNVERIFIED = "HOLD_UNVERIFIED"
    HOLD_TRIAL_LIMIT = "HOLD_TRIAL_LIMIT"
    HOLD_TOOL_ERROR = "HOLD_TOOL_ERROR"


@dataclass(frozen=True)
class GateDecision:
    """The result of :meth:`RoboticsGate.dispatch`. ``executed`` is true only for ``RELEASED``."""

    outcome: Outcome
    executed: bool
    tool_name: str
    tool_call_id: str
    reason: str | None = None
    tool_output: str | None = None
    verdict: str | None = None
    steering: str | None = None
    receipt_id: str | None = None
    kid: str | None = None
    receipt_class: ReceiptClass | None = None
    receipt_verified: bool = False
    latency_ms: float = 0.0

    @property
    def hold(self) -> bool:
        """True when the controller must hold (the host tool did not complete a release)."""
        return self.outcome is not Outcome.RELEASED


class ComplianceClient(Protocol):
    """The one method the gate needs from a ramen ai client."""

    @property
    def evaluate_compliance(self) -> Callable[..., Any]: ...


class GateUnavailableError(RuntimeError):
    """Raised by the local-mode client: there is no credential and the trial was not requested."""


class _LocalOnlyClient:
    def evaluate_compliance(self, input_text: str, **kwargs: Any) -> Any:
        raise GateUnavailableError(
            "no live evaluation in local mode; run with --trial (no key) or set RAMEN_API_KEY"
        )


@dataclass
class _Capture:
    input_text: str
    verdict: Mapping[str, Any] | None = None
    error: BaseException | None = None


class _RecordingClient:
    """Delegate to the real client and keep each evaluated text with its verdict or error."""

    def __init__(self, inner: ComplianceClient) -> None:
        self.inner = inner
        self.captures: list[_Capture] = []

    def evaluate_compliance(self, input_text: str, **kwargs: Any) -> Any:
        try:
            verdict = self.inner.evaluate_compliance(input_text, **kwargs)
        except BaseException as error:
            self.captures.append(_Capture(input_text, error=error))
            raise
        self.captures.append(_Capture(input_text, verdict=verdict if isinstance(verdict, Mapping) else None))
        return verdict


@dataclass
class _DispatchState:
    deadline: float | None
    capture_index: int
    hold: Outcome | None = None
    hold_reason: str | None = None
    released: bool = False
    host_error: BaseException | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class RoboticsGate:
    """Govern ``dispatch_manipulation`` (and the other industrial tools) before they reach a controller.

    Build one with :meth:`from_env` or pass a resolved :class:`GateConfig`. ``tools``
    maps tool names to the host's LangChain tools; names must be a subset of
    ``dispatch_manipulation``, ``adjust_plc_setpoint`` and ``mutate_safety_parameter``.
    ``client`` injects an ``evaluate_compliance`` client (tests, custom transports);
    otherwise the gate builds and owns one from ``config``.
    """

    def __init__(
        self,
        tools: Mapping[str, BaseTool],
        *,
        config: GateConfig,
        audit_path: str | None = None,
        public_keys: Mapping[str, str] | None = None,
        client: ComplianceClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._public_keys = dict(public_keys) if public_keys is not None else None
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._secrets: tuple[str, ...] = (config.api_key,) if config.api_key else ()
        self._owns_client = client is None
        try:
            inner = client if client is not None else _build_client(config)
        except Exception as error:
            raise GateConfigError(
                scrub(f"could not build the ramen ai client: {error}", self._secrets)
            ) from None
        self._inner = inner
        self._recorder = _RecordingClient(inner)
        self._state: _DispatchState | None = None
        self._last_live_at: float | None = None
        self._audit = AuditTrail(audit_path) if audit_path is not None else None
        wrapped = {
            name: ReleaseGuard.wrap(
                tool, check=self._release_check, on_release=self._on_release, on_error=self._on_host_error
            )
            for name, tool in tools.items()
        }
        self._agent = IndustrialAutomationAgent(client=cast(RamenClient, self._recorder), tools=wrapped)

    @classmethod
    def from_env(
        cls,
        tools: Mapping[str, BaseTool],
        *,
        trial: bool = False,
        environ: Mapping[str, str] | None = None,
        timeout_s: float = 10.0,
        deadline_s: float | None = None,
        min_interval_s: float | None = None,
        allow_custom_base_url: bool | None = None,
        audit_path: str | None = None,
        public_keys: Mapping[str, str] | None = None,
    ) -> RoboticsGate:
        """Resolve the mode from ``RAMEN_API_KEY``, ``trial`` and ``RAMEN_BASE_URL``; see :mod:`.config`."""
        config = resolve_config(
            trial=trial,
            environ=environ,
            allow_custom_base_url=allow_custom_base_url,
            timeout_s=timeout_s,
            deadline_s=deadline_s,
            min_interval_s=min_interval_s,
        )
        return cls(tools, config=config, audit_path=audit_path, public_keys=public_keys)

    @property
    def mode(self) -> str:
        return self.config.mode

    @property
    def tool_names(self) -> tuple[str, ...]:
        names: tuple[str, ...] = self._agent.tool_names
        return names

    @property
    def trial_quota(self) -> Mapping[str, int]:
        """The most recent trial allowance reported by the service (empty outside trial mode)."""
        quota = getattr(self._inner, "last_quota", None)
        return dict(quota) if isinstance(quota, Mapping) else {}

    def __repr__(self) -> str:
        return f"RoboticsGate(mode={self.config.mode!r}, base_url={self.config.base_url!r}, tools={self.tool_names!r})"

    def __enter__(self) -> RoboticsGate:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the client when the gate built it."""
        if self._owns_client:
            closer = getattr(self._inner, "close", None)
            if callable(closer):
                closer()

    def dispatch(
        self,
        tool_name: str,
        payload: Mapping[str, Any],
        *,
        tool_call_id: str | None = None,
    ) -> GateDecision:
        """Evaluate one resolved tool call and release it to the host tool only on a verified ALLOW."""
        call_id = tool_call_id or f"{tool_name}-direct"
        with self._lock:
            local_problem = (
                manipulation_payload_problem(payload) if tool_name == MANIPULATION_TOOL_NAME else None
            )
            if local_problem is None and self.config.mode != "local":
                self._pace()
            started = self._clock()
            deadline = started + self.config.deadline_s if self.config.deadline_s is not None else None
            state = _DispatchState(deadline=deadline, capture_index=len(self._recorder.captures))
            self._state = state
            command_error: str | None = None
            output: str | None = None
            try:
                command = self._agent.execute(tool_name, payload, tool_call_id=call_id)
                update: Mapping[str, Any] = command.update or {}
                command_error = update.get("governance_error")
                messages = update.get("messages") or []
                output = str(messages[0].content) if messages else None
            except Exception as error:  # any failure here is a hold
                command_error = f"gate could not run the dispatch: {error}"
            finally:
                self._state = None
            latency_ms = (self._clock() - started) * 1000.0
            captures = self._recorder.captures[state.capture_index :]
            del self._recorder.captures[:]
            capture = captures[-1] if captures else None
            if capture is not None:
                self._last_live_at = self._clock()
            decision = self._decide(
                tool_name, call_id, state, capture, local_problem, command_error, output, latency_ms
            )
            self._record(decision, capture)
            logger.info(
                "dispatch tool=%s id=%s outcome=%s receipt=%s class=%s latency_ms=%.1f",
                decision.tool_name,
                scrub(decision.tool_call_id, self._secrets),
                decision.outcome.value,
                decision.receipt_id,
                decision.receipt_class,
                decision.latency_ms,
            )
            return decision

    # -- release guard callbacks -------------------------------------------------

    def _release_check(self) -> None:
        state = self._state
        if state is None:
            raise HoldSignal(Outcome.HOLD_UNVERIFIED.value, "release outside a gate dispatch")
        captures = self._recorder.captures[state.capture_index :]
        capture = captures[-1] if captures else None
        if capture is None or capture.verdict is None or capture.verdict.get("allowed") is not True:
            state.hold, state.hold_reason = (
                Outcome.HOLD_UNVERIFIED,
                "release without an evaluated ALLOW verdict",
            )
            raise HoldSignal(state.hold.value, state.hold_reason)
        problem = receipt_problem(
            extract_receipt(capture.verdict), capture.input_text, allowed=True, public_keys=self._public_keys
        )
        if problem is not None:
            state.hold, state.hold_reason = Outcome.HOLD_UNVERIFIED, f"receipt did not verify: {problem}"
            raise HoldSignal(state.hold.value, state.hold_reason)
        if state.deadline is not None and self._clock() > state.deadline:
            state.hold = Outcome.HOLD_TIMEOUT
            state.hold_reason = (
                f"verified ALLOW arrived after the {self.config.deadline_s} s dispatch deadline"
            )
            raise HoldSignal(state.hold.value, state.hold_reason)

    def _on_release(self) -> None:
        if self._state is not None:
            self._state.released = True

    def _on_host_error(self, error: BaseException) -> None:
        if self._state is not None:
            self._state.host_error = error

    # -- internals ---------------------------------------------------------------

    def _pace(self) -> None:
        if self._last_live_at is None or self.config.min_interval_s <= 0:
            return
        wait = self.config.min_interval_s - (self._clock() - self._last_live_at)
        if wait > 0:
            self._sleep(wait)

    def _decide(
        self,
        tool_name: str,
        call_id: str,
        state: _DispatchState,
        capture: _Capture | None,
        local_problem: str | None,
        command_error: str | None,
        output: str | None,
        latency_ms: float,
    ) -> GateDecision:
        base: dict[str, Any] = {"tool_name": tool_name, "tool_call_id": call_id, "latency_ms": latency_ms}
        if capture is not None and capture.verdict is not None:
            verdict = capture.verdict
            allowed = verdict.get("allowed") is True
            receipt = extract_receipt(verdict)
            problem = receipt_problem(
                receipt, capture.input_text, allowed=allowed, public_keys=self._public_keys
            )
            steering = verdict.get("steering")
            base.update(
                verdict="ALLOW" if allowed else "BLOCK",
                steering=self._clean(steering) if isinstance(steering, str) and steering else None,
                receipt_id=_text(receipt, "id"),
                kid=_text(receipt, "kid"),
                receipt_class=receipt_class(receipt),
                receipt_verified=problem is None,
            )
        if state.released and state.host_error is None and command_error is None:
            return GateDecision(Outcome.RELEASED, True, tool_output=output, **base)
        if state.released:
            reason = f"host tool raised after release: {state.host_error or command_error}"
            return GateDecision(Outcome.HOLD_TOOL_ERROR, False, reason=self._clean(reason), **base)
        if local_problem is not None:
            return GateDecision(Outcome.LOCAL_BLOCK, False, reason=local_problem, **base)
        if capture is None:
            return GateDecision(Outcome.HOLD_UNAVAILABLE, False, reason=self._clean(command_error), **base)
        if capture.error is not None:
            if isinstance(capture.error, TrialLimitError):
                return GateDecision(
                    Outcome.HOLD_TRIAL_LIMIT, False, reason=describe_trial_limit(capture.error), **base
                )
            reason = f"evaluation unavailable: {type(capture.error).__name__}: {capture.error}"
            return GateDecision(Outcome.HOLD_UNAVAILABLE, False, reason=self._clean(reason), **base)
        if base.get("verdict") == "BLOCK":
            return GateDecision(
                Outcome.BLOCKED, False, reason=base.get("steering") or self._clean(command_error), **base
            )
        if state.hold is not None:
            return GateDecision(state.hold, False, reason=state.hold_reason, **base)
        return GateDecision(Outcome.HOLD_UNVERIFIED, False, reason=self._clean(command_error), **base)

    def _record(self, decision: GateDecision, capture: _Capture | None) -> None:
        if self._audit is None:
            return
        evaluated = capture is not None and capture.verdict is not None
        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "mode": self.config.mode,
            "tool_name": decision.tool_name,
            "tool_call_id": decision.tool_call_id,
            "outcome": decision.outcome.value,
            "executed": decision.executed,
            "evaluated": evaluated,
            "verdict": decision.verdict,
            "receipt_id": decision.receipt_id,
            "kid": decision.kid,
            "receipt_class": decision.receipt_class,
            "receipt_verified": decision.receipt_verified,
            "steering": decision.steering,
            "reason": decision.reason,
            "latency_ms": round(decision.latency_ms, 3),
        }
        if evaluated and capture is not None and capture.verdict is not None:
            record["input_text"] = capture.input_text
            record["receipt"] = extract_receipt(capture.verdict)
        self._audit.append(record)

    def _clean(self, text: object) -> str | None:
        return None if text is None else scrub(text, self._secrets)


def _text(receipt: Mapping[str, Any] | None, key: str) -> str | None:
    value = receipt.get(key) if receipt is not None else None
    return value if isinstance(value, str) and value else None


def _build_client(config: GateConfig) -> ComplianceClient:
    if config.mode == "keyed":
        assert config.api_key is not None
        return RamenClient(config.api_key, base_url=config.base_url, timeout=config.timeout_s)
    if config.mode == "trial":
        trial: ComplianceClient = TrialComplianceClient(base_url=config.base_url, timeout=config.timeout_s)
        return trial
    return _LocalOnlyClient()
