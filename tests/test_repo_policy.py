"""The repository passes its own hygiene and size guards, and the guards catch violations."""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tests.test_demos import load

ROOT = Path(__file__).resolve().parents[1]
hygiene = load(".github/scripts/check_hygiene.py")
sizes = load(".github/scripts/check_file_sizes.py")
SCENE = ROOT / "assets" / "scenes" / "stage1_cutlery_plunge.xml"


def _git_available() -> bool:
    return shutil.which("git") is not None and (ROOT / ".git").exists()


@pytest.mark.skipif(not _git_available(), reason="needs a git checkout")
def test_repository_is_clean() -> None:
    files = hygiene.list_files(ROOT)
    assert files, "no files listed"
    assert hygiene.scan(ROOT, files) == []
    assert sizes.scan(ROOT, files) == []


def _write(tmp_path: Path, name: str, content: str | bytes) -> str:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    return name


def test_hygiene_flags_planted_violations(tmp_path: Path) -> None:
    files = [
        _write(tmp_path, "a.py", "token = '" + "sk-" + "A" * 24 + "'\n"),
        _write(tmp_path, "b.py", "LOG = '/Us" + "ers/someone/project/file.txt'\n"),
        _write(tmp_path, "c.py", "class " + "IndustrialAutomationAgent:\n    pass\n"),
        _write(tmp_path, "d.md", "Built by " + "Ramen AI.\n"),
        _write(tmp_path, "e.md", "Simulation, " + "no hardware.\n"),
        _write(tmp_path, "AGENTS.md", "notes\n"),
        _write(tmp_path, "f.env.example", 'RAMEN_API_KEY="' + "x" * 12 + '"\n'),
    ]
    findings = hygiene.scan(tmp_path, files)
    for name in ("a.py", "b.py", "c.py", "d.md", "e.md", "AGENTS.md", "f.env.example"):
        assert any(finding.startswith(name) for finding in findings), name


def test_hygiene_allows_placeholders(tmp_path: Path) -> None:
    files = [_write(tmp_path, "ok.md", 'export RAMEN_API_KEY="<your ramen ai api key>"\nramen ai\n')]
    assert hygiene.scan(tmp_path, files) == []


def test_size_guard(tmp_path: Path) -> None:
    files = [
        _write(tmp_path, "big.txt", "x" * (256 * 1024 + 1)),
        _write(tmp_path, "mesh.obj", "v 0 0 0\n"),
        _write(tmp_path, "clip.mp4", b"\0\0"),
        _write(tmp_path, "docs/brand/ramen-logo.png", b"\0" * (64 * 1024 + 1)),
        _write(tmp_path, "docs/brand/other.png", b"\0"),
        _write(tmp_path, "scene.xml", "<mujoco/>"),
    ]
    findings = sizes.scan(tmp_path, files)
    flagged = {finding.split(":")[0] for finding in findings}
    assert flagged == {"big.txt", "mesh.obj", "clip.mp4", "docs/brand/ramen-logo.png", "docs/brand/other.png"}


def test_scene_uses_primitive_geoms_only() -> None:
    root = ET.parse(SCENE).getroot()  # noqa: S314 - repository file
    assert root.find(".//asset") is None
    assert all(geom.get("type", "sphere") != "mesh" for geom in root.iter("geom"))
    assert not [element for element in root.iter() if "file" in element.attrib]
    assert SCENE.stat().st_size < 16 * 1024
