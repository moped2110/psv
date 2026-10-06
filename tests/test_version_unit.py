"""``psv.__version__`` is the installed distribution's version, not a hand-typed copy.

It was hard-coded to ``"0.1.0"`` through two releases, and run records copy it into
``tool.version`` — so every record named the wrong tool release. Pin it to metadata.
"""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import psv
from psv.run_record import build_run_record

ROOT = Path(__file__).resolve().parent.parent


def test_version_is_the_installed_distribution_version() -> None:
    assert psv.__version__ == version("psv")


def test_installed_version_matches_pyproject() -> None:
    # Guards a stale editable install as much as the code: the metadata psv reads must be
    # the version this tree declares, or the first test would pin the wrong thing.
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert psv.__version__ == declared["project"]["version"]


def test_run_record_names_the_running_release() -> None:
    now = datetime(2026, 10, 6, tzinfo=UTC)
    record = build_run_record(
        command="reconcile",
        inputs={},
        report=None,
        exit_code=2,
        started_at=now,
        finished_at=now,
        error="unreachable",
    )
    assert record["tool"] == {"name": "psv", "version": version("psv")}
