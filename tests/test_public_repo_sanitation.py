from __future__ import annotations

from pathlib import Path

from tools.check_public_repo import ROOT, scan_file


def test_tracked_public_files_are_sanitized() -> None:
    from tools.check_public_repo import tracked_files

    findings = [finding for path in tracked_files() for finding in scan_file(path)]
    assert findings == []


def test_scanner_ignores_non_file_entries(tmp_path: Path) -> None:
    assert scan_file(tmp_path) == []


def test_scanner_reports_local_path(tmp_path) -> None:
    # Keep the fixture outside the repository so its deliberately hostile text is
    # not itself included in the tracked-file scan.
    path = tmp_path / "leak.txt"
    path.write_text("C:" + "\\" + "Users" + "\\" + "developer" + "\\" + "project")
    findings = scan_file(path)
    assert findings and findings[0].endswith("Windows user path")


def test_scanner_root_is_repository() -> None:
    assert (ROOT / "pyproject.toml").is_file()
