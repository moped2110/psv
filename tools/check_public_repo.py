"""Reject public-repository leaks of local paths and internal working context."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Windows user path", re.compile(r"(?i)[a-z]:[\\/]users[\\/]")),
    ("Unix user path", re.compile(r"/(?:home|users)/[^/\s]+/")),
    ("WSL mount path", re.compile(r"/mnt/[a-z]/", re.IGNORECASE)),
    ("portfolio workspace name", re.compile(r"cryptodominance", re.IGNORECASE)),
    ("internal instruction reference", re.compile(r"\b(?:CLAUDE|AGENTS)\.md\b")),
    ("personal attribution outside public handle", re.compile(r"\bMario(?:'s)?\b")),
)
SCAN_EXCLUSIONS = {".gitignore", "tools/check_public_repo.py"}


def tracked_files() -> list[Path]:
    """Return tracked and unignored candidate files for sanitation scanning."""
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [
        ROOT / relative
        for item in completed.stdout.split(b"\0")
        if item and (relative := item.decode().replace("\\", "/")) not in SCAN_EXCLUSIONS
    ]


def scan_file(path: Path) -> list[str]:
    """Return line-specific forbidden-content findings for one text file."""
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    findings: list[str] = []
    try:
        display_path = path.relative_to(ROOT)
    except ValueError:
        display_path = path
    for line_number, line in enumerate(text.splitlines(), start=1):
        for label, pattern in FORBIDDEN:
            if pattern.search(line):
                findings.append(f"{display_path}:{line_number}: {label}")
    return findings


def main() -> int:
    """Scan the public repository surface and return a shell-friendly status."""
    findings = [finding for path in tracked_files() for finding in scan_file(path)]
    if findings:
        print("Public-repository sanitation failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("Public-repository sanitation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
