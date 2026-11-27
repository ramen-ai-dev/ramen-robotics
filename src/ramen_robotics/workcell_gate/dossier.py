"""Tamper-evident audit dossier for one cell and period.

The dossier supports traceability records of software interventions under
ISO 10218-2:2025 (robot applications and robot cells) and Regulation (EU) 2023/1230
(applies from January 20, 2027). It does not state conformity with either; clause-level
mapping is tracked in ``docs/rfcs/0002-workcell-gate.md``.

Layout (``ramen-robotics.dossier/1``)::

    manifest.json        cell, period, versions, counts, chain anchor and head, SHA-256 of every file
    interventions.jsonl  workcell journal entries in the period (holds and releases)
    receipts.jsonl       the gate audit lines joined by tool_call_id (input text + Schema V5 receipt)
    verification.json    receipt, chain and join check results at export time
    DOSSIER.md           human-readable summary

Export refuses on a broken chain, a missing or inconsistent receipt join, any receipt that
does not verify, a different cell configuration, or a trial receipt unless ``allow_trial``.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

from ramen_robotics.gate import verify_audit_file

from .journal import GENESIS, read_entries, verify_chain
from .profile import CellProfile

DOSSIER_SCHEMA = "ramen-robotics.dossier/1"
FILES = ("interventions.jsonl", "receipts.jsonl", "verification.json", "DOSSIER.md")
TRIAL_NOTE = (
    "Receipts in this dossier come from the keyless public trial (`trial_<uuid>`). They verify "
    "under the pinned ramen ai key but are not persistent audit proof; keep a keyed run for records."
)
SCOPE_NOTE = (
    "This dossier supports traceability records of software interventions for a robot application "
    "and cell under ISO 10218-2:2025 and Regulation (EU) 2023/1230 (applies from January 20, 2027). "
    "Clause-level mapping is tracked in RFC 0002."
)


class DossierError(RuntimeError):
    """Export was refused. ``problems`` lists every reason."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = list(problems)


@dataclass(frozen=True)
class DossierManifest:
    path: Path
    evidence_class: str
    decisions: int
    by_outcome: Mapping[str, int]
    receipts_production: int
    receipts_trial: int
    chain_head: str
    files: Mapping[str, str]


def _parse_time(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _versions() -> dict[str, str]:
    versions = {"python": platform.python_version()}
    for name in ("ramen-robotics", "ramen-foundry", "ramen-ai-core"):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "unknown"
    return versions


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            if raw.strip():
                value = json.loads(raw)
                if isinstance(value, dict):
                    lines.append(value)
    return lines


def _join(
    entries: Sequence[Mapping[str, Any]], receipts: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Match each gate-stage entry to exactly one gate audit line with consistent fields."""
    index: dict[str, list[Mapping[str, Any]]] = {}
    for line in receipts:
        index.setdefault(str(line.get("tool_call_id")), []).append(line)
    joined: list[dict[str, Any]] = []
    problems: list[str] = []
    for entry in entries:
        if entry.get("stage") != "gate":
            continue
        call_id = str(entry.get("tool_call_id"))
        matches = index.get(call_id, [])
        if len(matches) != 1:
            problems.append(
                f"seq {entry.get('seq')}: expected one receipt line for {call_id}, found {len(matches)}"
            )
            continue
        line = matches[0]
        gate = entry.get("gate") or {}
        if line.get("outcome") != entry.get("outcome"):
            problems.append(f"seq {entry.get('seq')}: outcome differs between journal and receipt line")
        if line.get("receipt_id") != gate.get("receipt_id"):
            problems.append(f"seq {entry.get('seq')}: receipt id differs between journal and receipt line")
        joined.append(dict(line))
    return joined, problems


def export_dossier(
    journal_path: str | os.PathLike[str],
    receipts_path: str | os.PathLike[str],
    out_dir: str | os.PathLike[str],
    *,
    profile: CellProfile,
    since: datetime | None = None,
    until: datetime | None = None,
    allow_trial: bool = False,
    public_keys: Mapping[str, str] | None = None,
) -> DossierManifest:
    """Build and self-verify a dossier in ``out_dir`` (which must not exist yet)."""
    out = Path(out_dir)
    if out.exists():
        raise DossierError([f"{out} already exists; export into a new directory"])
    all_entries = read_entries(journal_path)
    problems = verify_chain(all_entries)
    selected: list[dict[str, Any]] = []
    for entry in all_entries:
        stamp = _parse_time(entry.get("timestamp"))
        if stamp is None:
            problems.append(f"seq {entry.get('seq')}: timestamp is not ISO 8601")
            continue
        if (since is None or stamp >= since) and (until is None or stamp <= until):
            selected.append(entry)
    if not selected:
        problems.append("no journal entries in the requested period")
    config_hash = profile.config_hash()
    if any(entry.get("config_hash") != config_hash for entry in selected):
        problems.append("entries were recorded under a different cell configuration than this profile")
    receipt_lines = _read_jsonl(Path(receipts_path)) if Path(receipts_path).is_file() else []
    joined, join_problems = _join(selected, receipt_lines)
    problems += join_problems
    trial = sum(
        1 for line in joined if line.get("evaluated") is True and line.get("receipt_class") == "trial"
    )
    production = sum(
        1 for line in joined if line.get("evaluated") is True and line.get("receipt_class") != "trial"
    )
    if trial and not allow_trial:
        problems.append(
            f"{trial} trial receipt(s) are not persistent audit proof; pass allow_trial=True to export "
            "a dossier labeled as trial evidence"
        )
    if problems:
        raise DossierError(problems)

    partial = out.with_name(out.name + ".partial")
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True, mode=0o700)
    try:
        _write(
            partial / "interventions.jsonl", "".join(json.dumps(e, sort_keys=True) + "\n" for e in selected)
        )
        _write(partial / "receipts.jsonl", "".join(json.dumps(r, sort_keys=True) + "\n" for r in joined))
        receipt_problems = verify_audit_file(
            partial / "receipts.jsonl", allow_trial=allow_trial, public_keys=public_keys
        )
        if receipt_problems:
            raise DossierError(receipt_problems)
        evidence_class = "trial" if trial else "production"
        anchor = str(selected[0]["prev_sha256"])
        head = str(selected[-1]["entry_sha256"])
        verification = {
            "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "receipts": receipt_problems,
            "chain": [],
            "join": [],
            "trial_receipts_allowed": allow_trial,
        }
        _write(partial / "verification.json", json.dumps(verification, indent=2, sort_keys=True) + "\n")
        by_outcome = dict(sorted(Counter(str(e["outcome"]) for e in selected).items()))
        by_stage = dict(sorted(Counter(str(e["stage"]) for e in selected).items()))
        summary = {
            "cell": profile.to_dict(),
            "config_hash": config_hash,
            "period": {
                "first": selected[0]["timestamp"],
                "last": selected[-1]["timestamp"],
                "since": since.isoformat() if since else None,
                "until": until.isoformat() if until else None,
            },
            "evidence_class": evidence_class,
            "counts": {
                "decisions": len(selected),
                "by_outcome": by_outcome,
                "by_stage": by_stage,
                "receipts": {"production": production, "trial": trial},
            },
            "chain": {"anchor_prev_sha256": anchor, "head_sha256": head, "entries": len(selected)},
        }
        _write(partial / "DOSSIER.md", render_markdown(summary, selected))
        files = {name: _sha256(partial / name) for name in FILES}
        manifest = {
            "schema": DOSSIER_SCHEMA,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "versions": _versions(),
            "anchors": {
                "ISO 10218-2:2025": "robot applications and robot cells",
                "Regulation (EU) 2023/1230": "machinery; applies from January 20, 2027",
            },
            **summary,
            "files": files,
        }
        _write(partial / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        partial.rename(out)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    return DossierManifest(
        path=out,
        evidence_class=evidence_class,
        decisions=len(selected),
        by_outcome=by_outcome,
        receipts_production=production,
        receipts_trial=trial,
        chain_head=head,
        files=files,
    )


def verify_dossier(
    out_dir: str | os.PathLike[str], *, public_keys: Mapping[str, str] | None = None
) -> list[str]:
    """Re-verify a dossier offline. Returns problems; an empty list means it is intact."""
    out = Path(out_dir)
    try:
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [f"manifest.json could not be read: {type(error).__name__}"]
    problems: list[str] = []
    if manifest.get("schema") != DOSSIER_SCHEMA:
        problems.append(f"manifest schema is not {DOSSIER_SCHEMA}")
    files = manifest.get("files") or {}
    for name in FILES:
        path = out / name
        if not path.is_file():
            problems.append(f"{name} is missing")
        elif files.get(name) != _sha256(path):
            problems.append(f"{name} does not match its manifest digest")
    if problems:
        return problems
    entries = read_entries(out / "interventions.jsonl")
    chain = manifest.get("chain") or {}
    problems += verify_chain(entries, anchor=str(chain.get("anchor_prev_sha256", GENESIS)))
    if not entries or entries[-1].get("entry_sha256") != chain.get("head_sha256"):
        problems.append("journal head does not match the manifest")
    if len(entries) != chain.get("entries"):
        problems.append("journal entry count does not match the manifest")
    if any(entry.get("config_hash") != manifest.get("config_hash") for entry in entries):
        problems.append("an entry was recorded under a different cell configuration")
    receipts = _read_jsonl(out / "receipts.jsonl")
    _, join_problems = _join(entries, receipts)
    problems += join_problems
    allow_trial = manifest.get("evidence_class") == "trial"
    problems += verify_audit_file(out / "receipts.jsonl", allow_trial=allow_trial, public_keys=public_keys)
    return problems


def _cell(value: Any) -> str:
    return str(value if value is not None else "-").replace("|", "/").replace("\n", " ")


def render_markdown(summary: Mapping[str, Any], entries: Sequence[Mapping[str, Any]]) -> str:
    """Deterministic Markdown summary of one dossier."""
    cell, counts, chain = summary["cell"], summary["counts"], summary["chain"]
    lines = [
        f"# Workcell audit dossier: {cell['cell_id']}",
        "",
        SCOPE_NOTE,
        "",
    ]
    if summary["evidence_class"] == "trial":
        lines += [f"> {TRIAL_NOTE}", ""]
    lines += [
        "| Field | Value |",
        "|---|---|",
        f"| Application | {_cell(cell['application'])} |",
        f"| Robots | {_cell(', '.join(cell['robot_ids']))} |",
        f"| Safeguards | {_cell(', '.join(cell['safeguards']) or '-')} |",
        f"| Collaborative operation | {_cell(', '.join(cell['collaborative_operation']) or '-')} |",
        f"| Configuration hash | `{summary['config_hash']}` |",
        f"| Period | {summary['period']['first']} to {summary['period']['last']} |",
        f"| Evidence class | {summary['evidence_class']} |",
        f"| Decisions | {counts['decisions']} |",
        f"| Receipts | {counts['receipts']['production']} production, {counts['receipts']['trial']} trial |",
        f"| Chain anchor / head | `{chain['anchor_prev_sha256'][:16]}…` / `{chain['head_sha256'][:16]}…` |",
        "",
        "## Outcomes",
        "",
        "| Outcome | Count |",
        "|---|---|",
        *(f"| {outcome} | {count} |" for outcome, count in counts["by_outcome"].items()),
        "",
        "## Interventions",
        "",
        "| Seq | Time (UTC) | Request | Stage | Outcome | Findings | Receipt |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry in entries:
        gate = entry.get("gate") or {}
        findings = ", ".join(str(item.get("code")) for item in entry.get("findings") or []) or "-"
        receipt = gate.get("receipt_id")
        receipt_cell = f"`{receipt}` ({gate.get('receipt_class')})" if receipt else "-"
        lines.append(
            f"| {entry.get('seq')} | {_cell(entry.get('timestamp'))} | {_cell(entry.get('request_kind'))}"
            f"{' (rush)' if entry.get('rush') else ''} | {entry.get('stage')} | {entry.get('outcome')} | "
            f"{_cell(findings)} | {receipt_cell} |"
        )
    lines += [
        "",
        "## Verification",
        "",
        "Re-check offline with `ramen_robotics.workcell_gate.verify_dossier(<this directory>)`: it recomputes "
        "every file digest, the journal hash chain, the journal-to-receipt join and every Schema V5 Ed25519 "
        "receipt.",
        "",
    ]
    return canonical_text("\n".join(lines))


def canonical_text(text: str) -> str:
    return text.rstrip() + "\n"
