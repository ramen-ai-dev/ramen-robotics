"""Binary and file-size guard.

Usage: python .github/scripts/check_file_sizes.py [ROOT]
Fails on any file over 256 KB and on media, mesh, weight and bag formats. One exact
path is exempt from the extension rule with its own 64 KB cap: the README emblem.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

MAX_BYTES = 256 * 1024
DENIED_SUFFIXES = frozenset(
    {
        ".mp4",
        ".mov",
        ".avi",
        ".webm",
        ".mkv",
        ".gif",
        ".png",
        ".jpg",
        ".jpeg",
        ".tif",
        ".tiff",
        ".bmp",
        ".exr",
        ".hdr",
        ".obj",
        ".stl",
        ".glb",
        ".gltf",
        ".dae",
        ".fbx",
        ".usd",
        ".usda",
        ".usdc",
        ".usdz",
        ".ply",
        ".npz",
        ".npy",
        ".pt",
        ".pth",
        ".ckpt",
        ".safetensors",
        ".onnx",
        ".h5",
        ".pkl",
        ".parquet",
        ".bag",
        ".mcap",
        ".db3",
        ".db",
        ".sqlite",
        ".zip",
        ".tar",
        ".gz",
        ".tgz",
        ".7z",
        ".whl",
    }
)
EXEMPT: dict[str, int] = {"docs/brand/ramen-logo.png": 64 * 1024}


def list_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],  # noqa: S607
        cwd=root,
        capture_output=True,
        check=True,
    )
    return sorted({name for name in result.stdout.decode().split("\0") if name and (root / name).is_file()})


def scan(root: Path, files: Iterable[str]) -> list[str]:
    findings: list[str] = []
    for name in files:
        size = (root / name).stat().st_size
        if name in EXEMPT:
            if size > EXEMPT[name]:
                findings.append(f"{name}: {size} bytes exceeds its {EXEMPT[name]} byte cap")
            continue
        suffix = Path(name).suffix.lower()
        if suffix in DENIED_SUFFIXES:
            findings.append(f"{name}: {suffix} files are not kept in git (see assets/README.md)")
        if size > MAX_BYTES:
            findings.append(f"{name}: {size} bytes exceeds the {MAX_BYTES} byte limit")
    return findings


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    files = list_files(root)
    findings = scan(root, files)
    for finding in findings:
        print(finding)
    print(f"check_file_sizes: {len(files)} files checked, {len(findings)} findings")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
