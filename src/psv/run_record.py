"""Structured, integrity-checked run records for auditable traceability.

Console output is ephemeral. For an audit trail we persist every reconciliation
as a JSON record — a UTC timestamp, the tool version, the exact invocation inputs,
the environment, the full report and the verdict — plus a one-line append to a
JSONL journal so a directory of runs stays greppable.

A content checksum over the canonical record (``runId``) detects accidental
modification. It is not an authenticity proof: an attacker who can rewrite the
file can also recompute the checksum. No secrets ever land in a record: an
``rpc_url`` is reduced to scheme+host so a provider key embedded in its
path/query cannot leak.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import __version__

SCHEMA_VERSION = "1.1"
_JOURNAL_LOCK = threading.Lock()

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
        try:
            port = parts.port
        except ValueError:
            return "<unparseable>"
        if port:
            host = f"{host}:{port}"
        return f"{parts.scheme}://{host}"
    return "<redacted>"


def _clean_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Remove secret-like fields and redact provider URLs before persistence."""
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
    """Compute the canonical SHA-256 integrity identifier for a record."""
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
    if not command.strip():
        raise ValueError("command must not be empty")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code < 0:
        raise ValueError("exit_code must be a non-negative integer")
    if started_at.tzinfo is None or finished_at.tzinfo is None:
        raise ValueError("run timestamps must be timezone-aware")
    if finished_at < started_at:
        raise ValueError("finished_at must not precede started_at")

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
    """Check the content checksum (integrity, not signer authenticity)."""
    claimed = record.get("runId")
    return isinstance(claimed, str) and claimed == _content_hash(record)


def _write_exclusive(path: Path, payload: bytes) -> None:
    """Create *path* exactly once and durably write *payload*."""
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    complete = False
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("failed to write run record")
            view = view[written:]
        os.fsync(fd)
        complete = True
    finally:
        os.close(fd)
        if not complete:
            path.unlink(missing_ok=True)


def _exclusive_record_path(log_dir: Path, stem: str, payload: bytes) -> Path:
    """Allocate and durably create a collision-free run-record path."""
    for sequence in range(10_000):
        suffix = "" if sequence == 0 else f"-{sequence}"
        path = log_dir / f"{stem}{suffix}.json"
        try:
            _write_exclusive(path, payload)
        except FileExistsError:
            continue
        return path
    raise FileExistsError(f"could not allocate a unique run-record filename in {log_dir}")


def _append_journal(path: Path, line: bytes) -> None:
    """Append one complete JSONL entry durably and without thread interleaving."""
    flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    # One append syscall keeps each short JSONL entry intact across processes;
    # the lock also prevents interleaving between threads in this process.
    with _JOURNAL_LOCK:
        fd = os.open(path, flags, 0o600)
        try:
            written = os.write(fd, line)
            if written != len(line):
                raise OSError("partial run journal write")
            os.fsync(fd)
        finally:
            os.close(fd)


def write_run_record(record: dict[str, Any], log_dir: Path) -> Path:
    """Create a unique record and append its summary to ``runs.jsonl``.

    Existing records are never overwritten, including when identical runs start
    in the same timestamp tick. The JSON file is created exclusively before its
    journal entry is appended.
    """
    if not verify_run_record(record):
        raise ValueError("refusing to persist a run record with an invalid checksum")
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = record["startedAt"].replace(":", "").replace("-", "").replace(".", "")
    short = record["runId"].removeprefix("sha256:")[:12]
    payload = (json.dumps(record, indent=2) + "\n").encode()
    path = _exclusive_record_path(log_dir, f"run-{ts}-{short}", payload)

    journal_line = {
        "runId": record["runId"],
        "startedAt": record["startedAt"],
        "command": record["command"],
        "consistent": record["consistent"],
        "exitCode": record["exitCode"],
        # Surface the reason for an exit-2 run in the index too (parity with #01,
        # T-22) so an unreachable/RPC-failed run is greppable without the full file.
        "error": record["error"],
        "file": path.name,
    }
    _append_journal(log_dir / "runs.jsonl", (json.dumps(journal_line) + "\n").encode())
    return path


def verify_run_journal(log_dir: Path) -> list[str]:
    """Return corruption findings for the journal and referenced records."""
    journal = log_dir / "runs.jsonl"
    if not journal.is_file():
        return ["runs.jsonl is missing"]
    findings: list[str] = []
    for line_number, raw in enumerate(journal.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            findings.append(f"runs.jsonl:{line_number}: invalid or truncated JSON")
            continue
        if not isinstance(entry, dict):
            findings.append(f"runs.jsonl:{line_number}: entry must be an object")
            continue
        filename = entry.get("file")
        if not isinstance(filename, str) or Path(filename).name != filename:
            findings.append(f"runs.jsonl:{line_number}: unsafe record filename")
            continue
        record_path = log_dir / filename
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            findings.append(f"runs.jsonl:{line_number}: record is missing or invalid")
            continue
        if not isinstance(record, dict) or not verify_run_record(record):
            findings.append(f"runs.jsonl:{line_number}: record checksum is invalid")
        elif entry.get("runId") != record.get("runId"):
            findings.append(f"runs.jsonl:{line_number}: journal/record runId mismatch")
    return findings
