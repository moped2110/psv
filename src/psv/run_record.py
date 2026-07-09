"""Structured, tamper-evident run records for auditable traceability.

Console output is ephemeral. For an audit trail we persist every reconciliation
as a JSON record — a UTC timestamp, the tool version, the exact invocation inputs,
the environment, the full report and the verdict — plus a one-line append to a
JSONL journal so a directory of runs stays greppable.

A content hash over the canonical record (``runId``) makes later tampering
detectable. No secrets ever land in a record: an ``rpc_url`` is reduced to
scheme+host so a provider key embedded in its path/query can't leak. psv holds no
signing keys at all (read-only, money invariant).
"""

from __future__ import annotations

import hashlib
import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import __version__

SCHEMA_VERSION = "1.0"

#: Default directory for run records (relative to the current working dir). Logging
#: is on by default; disable per run with ``--no-log`` or the env var below.
DEFAULT_LOG_DIR = "psv-runs"
#: Set this env var (to anything) to suppress the *default* run log — an explicit
#: ``--log-dir`` still writes. Used by the test suite to avoid polluting the tree.
NO_LOG_ENV = "PSV_NO_LOG"


def _redact_url(url: str | None) -> str | None:
    """Keep only scheme://host of an RPC URL — providers embed API keys in the
    path (``/v2/<KEY>``) or query, which must never be persisted."""
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable>"
    if parts.scheme and parts.hostname:
        host = parts.hostname
        if parts.port:
            host = f"{host}:{parts.port}"
        return f"{parts.scheme}://{host}"
    return "<redacted>"


def _clean_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in inputs.items():
        if v is None:
            continue
        if "key" in k.lower() or "secret" in k.lower() or "password" in k.lower():
            continue
        if k in ("rpc_url", "rpcUrl"):
            out[k] = _redact_url(str(v))
        else:
            out[k] = v
    return out


def _content_hash(record: dict[str, Any]) -> str:
    payload = {k: v for k, v in record.items() if k != "runId"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def build_run_record(
    *,
    command: str,
    inputs: dict[str, Any],
    report: dict[str, Any] | None,
    exit_code: int,
    started_at: datetime,
    finished_at: datetime,
    error: str | None = None,
) -> dict[str, Any]:
    """Assemble the full, self-describing record for one run (adds ``runId``).

    A run that produced no report — e.g. the chain/RPC was unreachable — is still
    recorded: pass ``error`` (a message) and the exit code (2). Such a run is never
    ``consistent`` and its ``report`` is null.
    """
    record: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "tool": {"name": "psv", "version": __version__},
        "command": command,
        "startedAt": started_at.astimezone(UTC).isoformat(),
        "finishedAt": finished_at.astimezone(UTC).isoformat(),
        "durationSeconds": round((finished_at - started_at).total_seconds(), 3),
        "inputs": _clean_inputs(inputs),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "report": report,
        "exitCode": exit_code,
        "consistent": error is None and exit_code == 0,
        "error": error,
    }
    record["runId"] = _content_hash(record)
    return record


def verify_run_record(record: dict[str, Any]) -> bool:
    """True iff the record's ``runId`` matches a fresh hash of its content."""
    claimed = record.get("runId")
    return isinstance(claimed, str) and claimed == _content_hash(record)


def write_run_record(record: dict[str, Any], log_dir: Path) -> Path:
    """Write the record as a timestamped JSON file and append a one-line summary
    to ``runs.jsonl`` in the same directory. Returns the JSON file path."""
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = record["startedAt"].replace(":", "").replace("-", "").replace(".", "")
    short = record["runId"].removeprefix("sha256:")[:12]
    path = log_dir / f"run-{ts}-{short}.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    journal_line = {
        "runId": record["runId"],
        "startedAt": record["startedAt"],
        "command": record["command"],
        "consistent": record["consistent"],
        "exitCode": record["exitCode"],
        "file": path.name,
    }
    with (log_dir / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(journal_line) + "\n")
    return path
