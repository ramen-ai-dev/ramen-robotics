"""Repository hygiene check: credentials, machine paths, copied core classes, prose rules.

Usage: python .github/scripts/check_hygiene.py [ROOT]
Scans tracked and untracked-but-not-ignored files. Exit status 1 lists every finding.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("OpenAI-style key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    (
        "GitHub token",
        re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}"),
    ),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("PyPI token", re.compile(r"\bpypi-[A-Za-z0-9_-]{40,}")),
    (
        "hardcoded RAMEN_API_KEY",
        re.compile(r"RAMEN_API_KEY\s*[:=]\s*[\"'](?!<)[^\"'\s]{8,}[\"']"),
    ),
)

PATH_PATTERN = re.compile(r"(?:/Users/[A-Za-z0-9._-]+|/home/[a-z][a-z0-9._-]*|[A-Za-z]:\\Users\\)")

# ramen foundry and ramen-ai-core own these; ramen robotics imports them.
CORE_DEFINITION = re.compile(
    r"^\s*(?:class\s+(?:IndustrialAutomationAgent|ManipulationDispatchPayload|TrialComplianceClient"
    r"|RamenToolNode|RamenTrialClient|RamenClient)\b"
    r"|def\s+(?:signed_verdict_problem|signed_allow_problem|manipulation_payload_problem|is_trial_receipt_id)\b)",
    re.MULTILINE,
)

# Repositories carry no agent instruction files.
FORBIDDEN_NAMES = frozenset({"agents.md", "claude.md", ".cursorrules"})

BRAND_PATTERN = re.compile(r"\bRamen[ -](?:AI|Ai|Foundry|Robotics|Forge)\b|\bRAMEN AI\b")
DISCLAIMER_PATTERN = re.compile(
    r"(?i)simulation, no hardware|not customer deployments|not a result we report|verdicts are semantic"
    r"|what this (?:page|repository|demo) does not claim"
)

SKIP_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg"})


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
        path = root / name
        if Path(name).name.lower() in FORBIDDEN_NAMES:
            findings.append(f"{name}: agent instruction files are not kept in this repository")
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        text = data.decode("utf-8", errors="replace")
        for label, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                findings.append(f"{name}:{_line(text, match.start())}: possible {label}")
        for match in PATH_PATTERN.finditer(text):
            findings.append(f"{name}:{_line(text, match.start())}: absolute machine path")
        if name.endswith(".py") and not name.startswith(".github/scripts/"):
            for match in CORE_DEFINITION.finditer(text):
                findings.append(
                    f"{name}:{_line(text, match.start())}: re-defines a ramen foundry or ramen-ai-core symbol; import it"
                )
        if name.endswith(".md"):
            for match in BRAND_PATTERN.finditer(text):
                findings.append(
                    f"{name}:{_line(text, match.start())}: write the brand in lowercase ({match.group()})"
                )
            for match in DISCLAIMER_PATTERN.finditer(text):
                findings.append(f"{name}:{_line(text, match.start())}: remove defensive disclaimer")
    return findings


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    files = list_files(root)
    findings = scan(root, files)
    for finding in findings:
        print(finding)
    print(f"check_hygiene: {len(files)} files scanned, {len(findings)} findings")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
