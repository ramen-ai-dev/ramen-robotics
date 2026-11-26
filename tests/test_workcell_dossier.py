"""Dossier export and offline verification, including tamper detection."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from demos.stage2_industrial_packaging import scenario as sc
from demos.stage2_industrial_packaging.packaging_cell_demo import make_host_tools

from ramen_robotics.gate import GateConfig, RoboticsGate
from ramen_robotics.workcell_gate import (
    DOSSIER_SCHEMA,
    CellProfile,
    DossierError,
    StaticStateProvider,
    WorkcellGate,
    export_dossier,
    read_entries,
    render_markdown,
    verify_dossier,
)
from tests.fakes import FAKE_API_KEY, FakeRamenServer
from tests.test_workcell_gate import workcell_decide


@dataclasses.dataclass
class Run:
    profile: CellProfile
    journal: Path
    receipts: Path
    tmp: Path


def run_rows(tmp_path: Path, *, trial: bool = False) -> Run:
    issuers = sc.Issuers.generate()
    profile = sc.build_profile(issuers)
    server = FakeRamenServer(decide=workcell_decide)
    receipts = tmp_path / "receipts.jsonl"
    journal = tmp_path / "workcell.jsonl"
    config = GateConfig(mode="trial") if trial else GateConfig(mode="keyed", api_key=FAKE_API_KEY)
    client = server.trial_client() if trial else server.keyed_client()
    gate = RoboticsGate(make_host_tools([]), config=config, client=client, audit_path=str(receipts))
    rows = sc.build_rows(issuers)
    provider = StaticStateProvider(rows[0].snapshot)
    workcell = WorkcellGate(profile, gate, provider, journal_path=journal, receipts_path=receipts)
    for row in rows:
        provider.set(row.snapshot)
        workcell.submit(row.request)
    return Run(profile, journal, receipts, tmp_path)


def export(run: Run, name: str = "dossier", **kwargs: Any) -> Path:
    out = run.tmp / name
    export_dossier(run.journal, run.receipts, out, profile=run.profile, **kwargs)
    return out


def rewrite(path: Path, edit: Any) -> None:
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    edit(lines)
    path.write_text("".join(json.dumps(line, sort_keys=True) + "\n" for line in lines))


def refresh_digest(out: Path, name: str) -> None:
    manifest = json.loads((out / "manifest.json").read_text())
    manifest["files"][name] = hashlib.sha256((out / name).read_bytes()).hexdigest()
    (out / "manifest.json").write_text(json.dumps(manifest))


def test_keyed_dossier_round_trip(tmp_path: Path) -> None:
    run = run_rows(tmp_path)
    out = run.tmp / "dossier"
    result = export_dossier(run.journal, run.receipts, out, profile=run.profile)
    assert result.evidence_class == "production" and result.decisions == 9
    assert result.receipts_production == 3 and result.receipts_trial == 0
    assert dict(result.by_outcome) == {"BLOCKED": 2, "LOCAL_BLOCK": 6, "RELEASED": 1}
    assert verify_dossier(out) == []
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["schema"] == DOSSIER_SCHEMA
    assert manifest["config_hash"] == run.profile.config_hash()
    assert manifest["counts"]["by_stage"] == {"gate": 3, "precheck": 6}
    assert set(manifest["versions"]) == {"python", "ramen-robotics", "ramen-foundry", "ramen-ai-core"}
    assert "ISO 10218-2:2025" in manifest["anchors"] and "Regulation (EU) 2023/1230" in manifest["anchors"]
    text = (out / "DOSSIER.md").read_text()
    assert "January 20, 2027" in text and "conform" not in text.lower()
    assert FAKE_API_KEY not in "".join(p.read_text() for p in out.iterdir())
    assert not (run.tmp / "dossier.partial").exists()


def test_trial_dossier_is_refused_by_default(tmp_path: Path) -> None:
    run = run_rows(tmp_path, trial=True)
    with pytest.raises(DossierError) as caught:
        export(run)
    assert all("trial receipt" in problem for problem in caught.value.problems)
    assert not (run.tmp / "dossier").exists()


def test_trial_dossier_when_allowed_is_labeled(tmp_path: Path) -> None:
    run = run_rows(tmp_path, trial=True)
    out = export(run, allow_trial=True)
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["evidence_class"] == "trial"
    assert manifest["counts"]["receipts"] == {"production": 0, "trial": 3}
    assert "not persistent audit proof" in (out / "DOSSIER.md").read_text()
    assert verify_dossier(out) == []


def test_relabeled_trial_dossier_fails_verification(tmp_path: Path) -> None:
    out = export(run_rows(tmp_path, trial=True), allow_trial=True)
    manifest = json.loads((out / "manifest.json").read_text())
    manifest["evidence_class"] = "production"
    (out / "manifest.json").write_text(json.dumps(manifest))
    assert any("trial receipt" in problem for problem in verify_dossier(out))


def test_edited_file_breaks_its_digest(tmp_path: Path) -> None:
    out = export(run_rows(tmp_path))
    rewrite(out / "interventions.jsonl", lambda lines: lines[0].update(outcome="RELEASED"))
    assert verify_dossier(out) == ["interventions.jsonl does not match its manifest digest"]


def test_edited_entry_with_refreshed_digest_breaks_the_chain(tmp_path: Path) -> None:
    out = export(run_rows(tmp_path))
    rewrite(out / "interventions.jsonl", lambda lines: lines[3].update(findings=[]))
    refresh_digest(out, "interventions.jsonl")
    assert any("entry 4" in problem for problem in verify_dossier(out))


def test_deleted_receipt_line_breaks_the_join(tmp_path: Path) -> None:
    out = export(run_rows(tmp_path))
    rewrite(out / "receipts.jsonl", lambda lines: lines.pop())
    refresh_digest(out, "receipts.jsonl")
    assert any("expected one receipt line" in problem for problem in verify_dossier(out))


def test_edited_receipt_text_fails_signature_binding(tmp_path: Path) -> None:
    out = export(run_rows(tmp_path))

    def edit(lines: list[dict[str, Any]]) -> None:
        lines[0]["input_text"] = lines[0]["input_text"].replace("MUTED", "ARMED")

    rewrite(out / "receipts.jsonl", edit)
    refresh_digest(out, "receipts.jsonl")
    assert any("payload_hash" in problem for problem in verify_dossier(out))


def test_missing_file_and_manifest(tmp_path: Path) -> None:
    out = export(run_rows(tmp_path))
    (out / "verification.json").unlink()
    assert "verification.json is missing" in verify_dossier(out)
    (out / "manifest.json").unlink()
    assert verify_dossier(out)[0].startswith("manifest.json could not be read")


def test_export_refuses_a_broken_journal(tmp_path: Path) -> None:
    run = run_rows(tmp_path)
    rewrite(run.journal, lambda lines: lines[2].update(outcome="RELEASED"))
    with pytest.raises(DossierError) as caught:
        export(run)
    assert any("entry 3" in problem for problem in caught.value.problems)


def test_export_refuses_missing_receipts(tmp_path: Path) -> None:
    run = run_rows(tmp_path)
    run.receipts.unlink()
    with pytest.raises(DossierError) as caught:
        export(run)
    assert sum("expected one receipt line" in problem for problem in caught.value.problems) == 3


def test_export_refuses_another_cell_configuration(tmp_path: Path) -> None:
    run = run_rows(tmp_path)
    run.profile = dataclasses.replace(run.profile, collaborative_zone_m=2.0)
    with pytest.raises(DossierError, match="different cell configuration"):
        export(run)


def test_export_refuses_existing_directory_and_empty_period(tmp_path: Path) -> None:
    run = run_rows(tmp_path)
    (run.tmp / "taken").mkdir()
    with pytest.raises(DossierError, match="already exists"):
        export(run, "taken")
    future = datetime.now(timezone.utc) + timedelta(days=1)
    with pytest.raises(DossierError, match="no journal entries"):
        export(run, since=future)


def test_period_export_anchors_mid_chain(tmp_path: Path) -> None:
    run = run_rows(tmp_path)
    entries = read_entries(run.journal)
    since = datetime.fromisoformat(entries[6]["timestamp"])
    out = export(run, since=since)
    manifest = json.loads((out / "manifest.json").read_text())
    exported = read_entries(out / "interventions.jsonl")
    assert exported[0]["seq"] >= 7 - 1  # millisecond timestamps may include a neighbour
    assert manifest["chain"]["anchor_prev_sha256"] == exported[0]["prev_sha256"]
    assert verify_dossier(out) == []


def test_markdown_is_deterministic(tmp_path: Path) -> None:
    out = export(run_rows(tmp_path))
    manifest = json.loads((out / "manifest.json").read_text())
    entries = read_entries(out / "interventions.jsonl")
    assert render_markdown(manifest, entries) == render_markdown(manifest, entries)
    assert render_markdown(manifest, entries) == (out / "DOSSIER.md").read_text()
