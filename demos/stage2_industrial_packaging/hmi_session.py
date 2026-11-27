"""Socket-free controller behind the Stage 2 workcell monitor.

One session owns one run: fresh MES, sanitation and lockout issuers, the cell profile,
a ``RoboticsGate`` in the selected mode and a ``WorkcellGate`` journaling into a new run
directory. ``hmi_http`` only translates HTTP requests into these calls, so everything
here is tested without a listener.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from ramen_robotics.gate import (
    Outcome,
    RoboticsGate,
    describe_mode,
    receipt_class,
    receipt_problem,
    resolve_config,
)
from ramen_robotics.workcell_gate import (
    BatchStart,
    DossierError,
    RecipeExport,
    SafetyParameterChange,
    SetpointChange,
    StaticStateProvider,
    WorkcellDecision,
    WorkcellGate,
    export_dossier,
    read_entries,
    verify_chain,
    verify_dossier,
)
from ramen_robotics.workcell_gate.journal import GENESIS
from ramen_robotics.workcell_gate.profile import json_safe
from ramen_robotics.workcell_gate.requests import request_summary

try:  # repository root on sys.path (tests, editable installs)
    from demos.stage2_industrial_packaging.packaging_cell_demo import (
        DEFAULT_DEADLINE_S,
        TITLE,
        make_host_tools,
    )
    from demos.stage2_industrial_packaging.scenario import (
        FREEZER,
        LIGHT_CURTAIN,
        Issuers,
        Row,
        build_profile,
        build_rows,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from packaging_cell_demo import DEFAULT_DEADLINE_S, TITLE, make_host_tools  # type: ignore[no-redef]
    from scenario import (  # type: ignore[no-redef]
        FREEZER,
        LIGHT_CURTAIN,
        Issuers,
        Row,
        build_profile,
        build_rows,
    )

Mode = Literal["local", "trial", "keyed"]
MODES: tuple[Mode, ...] = ("local", "trial", "keyed")
DEFAULT_RUN_ROOT = Path("out") / "stage2_hmi"
ROW_COUNT = 9
NOT_EVALUATED = "not evaluated, live required"
LIGHT_CURTAIN_SAFEGUARD = "light_curtain_infeed_a"
LOCAL_MODE_LINE = (
    "Mode: local workcell checks only, 0 requests. Restart with --trial (no key) or --keyed "
    "(RAMEN_API_KEY) for live verdicts on rows 7 to 9."
)
TRIAL_DOSSIER_HINT = (
    "Restart the monitor with --allow-trial-dossier to export a dossier labeled evidence_class: trial."
)


class SessionError(RuntimeError):
    """A request the session refuses. ``code`` is stable; ``status`` is the HTTP status to use."""

    def __init__(
        self, code: str, detail: str, *, status: int = 409, extra: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status
        self.extra = dict(extra or {})


def evidence_label(production: int, trial: int) -> str:
    """What a dossier's receipts prove, without calling a receipt-free run "production"."""
    if trial:
        return "trial"
    if production:
        return "production"
    return "local checks only (0 receipts)"


def token_claims(token: str | None) -> dict[str, Any]:
    """Decode the claims of a ``v1.<kid>.<claims>.<signature>`` token for display only.

    The workcell gate verifies the signature; this helper never does and is never used
    for a decision.
    """
    if not token:
        return {}
    parts = token.split(".")
    if len(parts) != 4:
        return {}
    body = parts[2]
    try:
        value = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    lines: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
            except ValueError:
                continue
            if isinstance(value, dict):
                lines.append(value)
    return lines


def _schema_version(receipt: Mapping[str, Any] | None) -> str | None:
    if receipt is None:
        return None
    try:
        payload = json.loads(str(receipt.get("canonical_payload")))
    except ValueError:
        return None
    version = payload.get("schema_version") if isinstance(payload, dict) else None
    return str(version) if version is not None else None


class HmiSession:
    """Run the nine rush-order rows one request at a time and expose the run as JSON-ready dicts.

    Every public method that changes state takes the session lock without waiting and
    raises ``SessionError("busy")`` when another request holds it, so a slow live
    evaluation never queues a second one behind it.
    """

    def __init__(
        self,
        mode: Mode,
        *,
        environ: Mapping[str, str],
        run_root: Path = DEFAULT_RUN_ROOT,
        client: Any = None,
        gate_options: Mapping[str, Any] | None = None,
        allow_trial_dossier: bool = False,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown mode: {mode!r}")
        self.mode: Mode = mode
        self.allow_trial_dossier = allow_trial_dossier
        self._environ = dict(environ)
        self._run_root = Path(run_root)
        self._client = client
        self._gate_options = {"deadline_s": DEFAULT_DEADLINE_S, **(gate_options or {})}
        self._lock = threading.Lock()
        self._gate: RoboticsGate | None = None
        self._start()

    # -- lifecycle ---------------------------------------------------------------

    def _start(self) -> None:
        config = resolve_config(trial=self.mode == "trial", environ=self._environ, **self._gate_options)
        if config.mode != self.mode:
            raise ValueError(f"the environment resolves to {config.mode} mode, not {self.mode}")
        self.run_dir = self._new_run_dir()
        self.journal_path = self.run_dir / "workcell.jsonl"
        self.receipts_path = self.run_dir / "receipts.jsonl"
        self.issuers = Issuers.generate()
        self.profile = build_profile(self.issuers)
        profile_path = self.run_dir / "profile.json"  # public issuer keys only
        profile_path.write_text(
            json.dumps(self.profile.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        self.reached: list[str] = []
        self._gate = RoboticsGate(
            make_host_tools(self.reached),
            config=config,
            audit_path=str(self.receipts_path),
            client=self._client,
        )
        self._provider = StaticStateProvider(self._row(1).snapshot)
        self.workcell = WorkcellGate(
            self.profile,
            self._gate,
            self._provider,
            journal_path=self.journal_path,
            receipts_path=self.receipts_path,
        )
        self.results: dict[int, dict[str, Any]] = {}
        self.limit_reached = False
        self.dossier: dict[str, Any] | None = None
        self._exports = 0

    def _new_run_dir(self) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        for suffix in range(100):
            candidate = self._run_root / (stamp if suffix == 0 else f"{stamp}-{suffix}")
            try:
                candidate.mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                continue
            return candidate
        raise RuntimeError("could not create a new run directory")

    @property
    def gate(self) -> RoboticsGate:
        assert self._gate is not None
        return self._gate

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def close(self) -> None:
        if self._gate is not None:
            self._gate.close()

    def reset(self) -> dict[str, Any]:
        """Start a new run: new issuers, profile, run directory and gate. Earlier runs stay on disk."""
        with self._exclusive():
            self.close()
            self._start()
        return self.session_view()

    def _exclusive(self) -> _Exclusive:
        return _Exclusive(self._lock)

    # -- rows --------------------------------------------------------------------

    def _row(self, number: int) -> Row:
        """Build row ``number`` with fresh evidence tokens (they expire an hour after issue)."""
        if not 1 <= number <= ROW_COUNT:
            raise SessionError("not_found", f"row {number} does not exist", status=404)
        return build_rows(self.issuers)[number - 1]

    def run_row(self, number: int) -> dict[str, Any]:
        """Submit one row through the workcell gate. Live rows make at most one evaluation request."""
        row = self._row(number)
        with self._exclusive():
            if number in self.results:
                raise SessionError(
                    "already_run", f"row {number} already ran in this run; reset to start a new run"
                )
            if row.live and self.mode == "local":
                raise SessionError(
                    "live_disabled", f"row {number}: {NOT_EVALUATED}; restart with --trial or --keyed"
                )
            if row.live and self.limit_reached:
                raise SessionError(
                    "trial_limit", "the trial allowance is used up for today; live rows are not sent"
                )
            self._provider.set(row.snapshot)
            started = time.perf_counter()
            decision = self.workcell.submit(row.request)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if decision.outcome is Outcome.HOLD_TRIAL_LIMIT:
                self.limit_reached = True
            result = self._result(row, decision, elapsed_ms)
            self.results[number] = result
            return result

    def _result(self, row: Row, decision: WorkcellDecision, elapsed_ms: float) -> dict[str, Any]:
        if decision.outcome is Outcome.HOLD_TRIAL_LIMIT:
            status = "limit"
        else:
            matched = decision.outcome is row.expected and (
                row.expected_code is None or row.expected_code in decision.codes
            )
            status = "ok" if matched else "unexpected"
        gate = decision.gate
        return {
            "number": row.number,
            "status": status,
            "outcome": decision.outcome.value,
            "expected": row.expected.value,
            "expected_code": row.expected_code.value if row.expected_code else None,
            "stage": decision.stage,
            "request_kind": decision.request_kind,
            "tool_call_id": decision.tool_call_id,
            "rush": decision.rush,
            "executed": gate.executed if gate is not None else False,
            "findings": [finding.to_dict() for finding in decision.findings],
            "reason": decision.reason,
            "elapsed_ms": round(elapsed_ms, 1),
            "snapshot_digest": decision.snapshot_digest,
            "config_hash": decision.config_hash,
            "gate": None
            if gate is None
            else {
                "verdict": gate.verdict,
                "steering": gate.steering,
                "receipt_id": gate.receipt_id,
                "kid": gate.kid,
                "receipt_class": gate.receipt_class,
                "receipt_verified": gate.receipt_verified,
                "latency_ms": round(gate.latency_ms, 1),
            },
        }

    # -- views -------------------------------------------------------------------

    def endpoint(self) -> str | None:
        config = self.gate.config
        if config.mode == "trial":
            return f"{config.base_url}/api/v1/paas/try"
        if config.mode == "keyed":
            return f"{config.base_url}/api/v1/paas/evaluate"
        return None

    def rows_view(self) -> list[dict[str, Any]]:
        results = dict(self.results)
        views = []
        for row in build_rows(self.issuers):
            unavailable = row.live and self.mode == "local"
            if unavailable:
                note = NOT_EVALUATED
            elif row.live:
                note = "1 live evaluation"
            else:
                note = "local check, 0 requests"
            views.append(
                {
                    "number": row.number,
                    "area": row.area,
                    "step": row.step,
                    "request_kind": type(row.request).__name__,
                    "expected": row.expected.value,
                    "expected_code": row.expected_code.value if row.expected_code else None,
                    "live": row.live,
                    "available": not unavailable,
                    "note": note,
                    "result": results.get(row.number),
                }
            )
        return views

    def session_view(self) -> dict[str, Any]:
        results = list(dict(self.results).values())
        by_outcome: dict[str, int] = {}
        for result in results:
            by_outcome[result["outcome"]] = by_outcome.get(result["outcome"], 0) + 1
        profile = self.profile
        return {
            "title": TITLE,
            "mode": self.mode,
            "mode_line": LOCAL_MODE_LINE if self.mode == "local" else describe_mode(self.gate),
            "endpoint": self.endpoint(),
            "cell": {
                "cell_id": profile.cell_id,
                "application": profile.application,
                "robot_ids": list(profile.robot_ids),
                "safeguards": list(profile.safeguards),
                "collaborative_operation": list(profile.collaborative_operation),
                "config_hash": profile.config_hash(),
            },
            "run_dir": str(self.run_dir),
            "journal_path": str(self.journal_path),
            "receipts_path": str(self.receipts_path),
            "rows": self.rows_view(),
            "quota": dict(self.gate.trial_quota),
            "trial_limit_reached": self.limit_reached,
            "allow_trial_dossier": self.allow_trial_dossier,
            "reached": list(self.reached),
            "busy": self.busy,
            "summary": {
                "ran": len(results),
                "ok": sum(1 for result in results if result["status"] == "ok"),
                "unexpected": sum(1 for result in results if result["status"] == "unexpected"),
                "live_requests": sum(1 for result in results if result["stage"] == "gate"),
                "by_outcome": by_outcome,
            },
        }

    def cell_view(self, number: int) -> dict[str, Any]:
        """Cell state and request for one row, shaped for the schematic. Tokens appear as digests only."""
        row = self._row(number)
        snapshot, request, profile = row.snapshot, row.request, self.profile
        recipe = request.recipe_id if isinstance(request, BatchStart) else snapshot.active_recipe_id
        required = sorted(profile.recipe_tooling.get(recipe, frozenset()))
        envelope = profile.speed_envelope(snapshot.mode)
        freezer = profile.plc_envelope(*FREEZER)
        sensors = {
            reading.sensor_id: {
                "kind": reading.kind,
                "confidence": reading.confidence,
                "age_s": reading.age_s,
                "faults": list(reading.faults),
                "healthy": reading.confidence >= profile.sensor_min_confidence
                and reading.age_s <= profile.sensor_max_age_s
                and not reading.faults,
            }
            for reading in snapshot.sensors
        }
        previous = snapshot.previous_recipe_id
        work_order = None
        if isinstance(request, BatchStart):
            claims = token_claims(request.work_order_token)
            work_order = {
                "work_order_id": request.work_order_id,
                "lot_id": request.lot_id,
                "recipe_id": request.recipe_id,
                "requested_quantity": request.quantity,
                "signed_quantity": claims.get("quantity"),
            }
        requested_c = None
        if isinstance(request, SetpointChange) and (request.node_id, request.tag) == FREEZER:
            requested_c = request.target_val
        curtain: dict[str, Any] = {
            "node_id": LIGHT_CURTAIN[0],
            "tag": LIGHT_CURTAIN[1],
            "safeguard_ok": snapshot.safeguards_ok.get(LIGHT_CURTAIN_SAFEGUARD),
            "mute_requested": False,
        }
        if isinstance(request, SafetyParameterChange):
            curtain.update(
                mute_requested=True,
                mutation_type=request.mutation_type,
                requested_value=request.requested_value,
                duration_sec=request.duration_sec,
                key_evidence_presented=request.key_evidence_token is not None,
                management_of_change_id=request.management_of_change_id,
            )
        egress: dict[str, Any] = {
            "requested": False,
            "allowed_destinations": sorted(profile.egress.allowed_destinations),
        }
        if isinstance(request, RecipeExport):
            egress.update(
                requested=True,
                recipe_id=request.recipe_id,
                destination=request.destination,
                fields=list(request.fields),
                allowed=request.destination in profile.egress.allowed_destinations,
            )
        return {
            "number": row.number,
            "area": row.area,
            "step": row.step,
            "request_kind": type(request).__name__,
            "rush": bool(getattr(request, "rush", False)),
            "mode": snapshot.mode,
            "work_order": work_order,
            "sanitation": {
                "previous_recipe_id": previous,
                "next_recipe_id": snapshot.active_recipe_id,
                "previous_allergens": sorted(profile.allergen_matrix.get(previous or "", frozenset())),
                "next_allergens": sorted(profile.allergen_matrix.get(snapshot.active_recipe_id, frozenset())),
                "clearance_presented": snapshot.sanitation_token is not None,
            },
            "gripper": {
                "recipe_id": recipe,
                "mounted": snapshot.mounted_gripper_id,
                "required": required,
                "match": snapshot.mounted_gripper_id in required,
            },
            "sensors": sensors,
            "sensor_min_confidence": profile.sensor_min_confidence,
            "freezer": {
                "node_id": FREEZER[0],
                "tag": FREEZER[1],
                "current_c": snapshot.plc_values.get(FREEZER),
                "requested_c": requested_c,
                "min_c": freezer.min_val if freezer else None,
                "max_c": freezer.max_val if freezer else None,
                "envelope_id": freezer.operating_envelope_id if freezer else None,
            },
            "separation": {
                "nearest_human_m": snapshot.nearest_human_m,
                "collaborative_zone_m": profile.collaborative_zone_m,
                "min_separation_m": envelope.min_separation_m if envelope else None,
                "max_tcp_speed_mps": envelope.max_tcp_speed_mps if envelope else None,
                "commanded_speed_mps": getattr(request, "commanded_velocity_mps", None),
            },
            "light_curtain": curtain,
            "egress": egress,
            "request": json_safe(request_summary(request)),
            "result": dict(self.results).get(row.number),
        }

    def journal_view(self) -> dict[str, Any]:
        entries = read_entries(self.journal_path) if self.journal_path.is_file() else []
        problems = verify_chain(entries)
        items = []
        previous = GENESIS
        for entry in entries:
            gate = entry.get("gate") or {}
            items.append(
                {
                    "seq": entry.get("seq"),
                    "timestamp": entry.get("timestamp"),
                    "request_kind": entry.get("request_kind"),
                    "rush": entry.get("rush"),
                    "stage": entry.get("stage"),
                    "outcome": entry.get("outcome"),
                    "codes": [finding.get("code") for finding in entry.get("findings") or []],
                    "receipt_id": gate.get("receipt_id"),
                    "receipt_class": gate.get("receipt_class"),
                    "tool_call_id": entry.get("tool_call_id"),
                    "prev_sha256": entry.get("prev_sha256"),
                    "entry_sha256": entry.get("entry_sha256"),
                    "linked": entry.get("prev_sha256") == previous,
                }
            )
            previous = str(entry.get("entry_sha256"))
        return {
            "path": str(self.journal_path),
            "entries": items,
            "chain_ok": not problems,
            "problems": problems,
            "anchor": GENESIS,
            "head": entries[-1].get("entry_sha256") if entries else GENESIS,
        }

    def receipts_view(self) -> dict[str, Any]:
        """Re-verify every evaluated receipt against the pinned ramen ai keys."""
        items = []
        for line in _read_jsonl(self.receipts_path):
            if line.get("evaluated") is not True:
                continue
            raw = line.get("receipt")
            receipt = raw if isinstance(raw, dict) else None
            verdict = line.get("verdict")
            text = line.get("input_text")
            if verdict in ("ALLOW", "BLOCK") and isinstance(text, str):
                problem = receipt_problem(receipt, text, allowed=verdict == "ALLOW")
            else:
                problem = "missing verdict or input_text"
            items.append(
                {
                    "tool_call_id": line.get("tool_call_id"),
                    "receipt_id": receipt.get("id") if receipt else None,
                    "kid": receipt.get("kid") if receipt else None,
                    "schema_version": _schema_version(receipt),
                    "receipt_class": receipt_class(receipt),
                    "verdict": verdict,
                    "outcome": line.get("outcome"),
                    "verified": problem is None,
                    "problem": problem,
                    "steering": line.get("steering"),
                    "latency_ms": line.get("latency_ms"),
                    "input_text": text,
                }
            )
        production = sum(1 for item in items if item["receipt_class"] == "production")
        trial = sum(1 for item in items if item["receipt_class"] == "trial")
        return {
            "path": str(self.receipts_path),
            "receipts": items,
            "production": production,
            "trial": trial,
            "all_verified": all(item["verified"] for item in items),
        }

    def dossier_view(self) -> dict[str, Any]:
        return dict(self.dossier) if self.dossier is not None else {"exported": False}

    def export(self) -> dict[str, Any]:
        """Export and re-verify a dossier for this run into a new ``dossier-NN`` directory."""
        with self._exclusive():
            if not self.results:
                raise SessionError("no_decisions", "run at least one request before exporting a dossier")
            self._exports += 1
            out = self.run_dir / f"dossier-{self._exports:02d}"
            try:
                manifest = export_dossier(
                    self.journal_path,
                    self.receipts_path,
                    out,
                    profile=self.profile,
                    allow_trial=self.allow_trial_dossier,
                )
            except DossierError as error:
                trial_only = bool(error.problems) and all(
                    "trial receipt" in problem for problem in error.problems
                )
                refused_for_trial = trial_only and not self.allow_trial_dossier
                raise SessionError(
                    "dossier_refused",
                    "; ".join(error.problems),
                    extra={
                        "problems": error.problems,
                        "trial_refused": refused_for_trial,
                        "hint": TRIAL_DOSSIER_HINT if refused_for_trial else None,
                    },
                ) from None
            problems = verify_dossier(out)
            receipts = manifest.receipts_production + manifest.receipts_trial
            self.dossier = {
                "exported": True,
                "path": str(manifest.path),
                "evidence_class": manifest.evidence_class if receipts else None,
                "evidence_label": evidence_label(manifest.receipts_production, manifest.receipts_trial),
                "decisions": manifest.decisions,
                "by_outcome": dict(manifest.by_outcome),
                "receipts": {
                    "production": manifest.receipts_production,
                    "trial": manifest.receipts_trial,
                },
                "chain_head": manifest.chain_head,
                "files": dict(manifest.files),
                "verify": problems,
                "intact": not problems,
                "markdown": (out / "DOSSIER.md").read_text(encoding="utf-8"),
            }
            return dict(self.dossier)


class _Exclusive:
    """Take a lock without waiting; raise ``SessionError("busy")`` when it is held."""

    def __init__(self, lock: threading.Lock) -> None:
        self._lock = lock

    def __enter__(self) -> None:
        if not self._lock.acquire(blocking=False):
            raise SessionError("busy", "another request is running; try again when it finishes")

    def __exit__(self, *_: object) -> None:
        self._lock.release()
