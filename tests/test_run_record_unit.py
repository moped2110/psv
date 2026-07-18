"""Tests for psv structured, integrity-checked run records."""

from __future__ import annotations

import errno
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from reconcile_fakes import TX_HASH, strict_token

from psv.cli import main
from psv.rails import get_rail
from psv.run_record import (
    _clean_inputs,
    _redact_url,
    build_run_record,
    verify_run_journal,
    verify_run_record,
    write_run_record,
)

PAYER = "0x" + "11" * 20
PAYEE = "0x" + "22" * 20
NONCE = "0x" + "ab" * 32


def _record() -> dict[str, Any]:
    start = datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC)
    end = datetime(2026, 7, 9, 12, 0, 2, tzinfo=UTC)
    return build_run_record(
        command="reconcile",
        inputs={"rail": "usdc-base", "rpc_url": None, "secret_key": "0xdead"},
        report={"divergence": {"kind": "phantom_credit", "isFailure": True}},
        exit_code=1,
        started_at=start,
        finished_at=end,
    )


def test_record_core_fields_and_hash() -> None:
    rec = _record()
    assert rec["tool"]["name"] == "psv"
    assert rec["command"] == "reconcile"
    assert rec["durationSeconds"] == 2.0
    assert rec["exitCode"] == 1
    assert rec["consistent"] is False
    assert rec["report"]["divergence"]["kind"] == "phantom_credit"
    assert rec["runId"].startswith("sha256:")


def test_inputs_drop_secrets_and_none() -> None:
    rec = _record()
    assert "secret_key" not in rec["inputs"]
    assert "rpc_url" not in rec["inputs"]  # None dropped
    assert rec["inputs"]["rail"] == "usdc-base"


def test_verify_detects_tampering() -> None:
    rec = _record()
    assert verify_run_record(rec) is True
    rec["exitCode"] = 0  # pretend it was consistent
    assert verify_run_record(rec) is False


def test_redact_url_strips_key() -> None:
    assert _redact_url("https://polygon.g.alchemy.com/v2/KEY") == "https://polygon.g.alchemy.com"
    assert _redact_url(None) is None


def test_redact_url_rejects_invalid_port_without_raising() -> None:
    assert _redact_url("http://example.com:notaport/v2/KEY") == "<unparseable>"


def test_redact_url_handles_malformed_and_relative_values_without_leaking() -> None:
    assert _redact_url("http://[broken-ipv6") == "<unparseable>"
    assert _redact_url("relative/path/with/key") == "<redacted>"


def test_clean_inputs_redacts_rpc() -> None:
    cleaned = _clean_inputs({"rpc_url": "https://n.example/v2/KEY", "rail": "eurc-base"})
    assert cleaned == {"rpc_url": "https://n.example", "rail": "eurc-base"}


def test_write_record_file_and_journal(tmp_path) -> None:
    rec = _record()
    path = write_run_record(rec, tmp_path)
    assert path.exists()
    assert verify_run_record(json.loads(path.read_text())) is True
    line = json.loads((tmp_path / "runs.jsonl").read_text().strip())
    assert line["runId"] == rec["runId"]
    assert line["consistent"] is False
    # The journal must carry the verdict + reason (T-22 parity with #01).
    assert line["exitCode"] == 1
    assert line["error"] is None


def test_identical_records_never_overwrite(tmp_path) -> None:
    rec = _record()
    first = write_run_record(rec, tmp_path)
    second = write_run_record(rec, tmp_path)
    assert first != second
    assert first.exists() and second.exists()
    assert len((tmp_path / "runs.jsonl").read_text().splitlines()) == 2


def test_parallel_writes_have_unique_files_and_valid_journal(tmp_path) -> None:
    rec = _record()
    with ThreadPoolExecutor(max_workers=8) as executor:
        paths = list(executor.map(lambda _: write_run_record(rec, tmp_path), range(32)))
    assert len(set(paths)) == 32
    assert all(verify_run_record(json.loads(path.read_text())) for path in paths)
    lines = [json.loads(line) for line in (tmp_path / "runs.jsonl").read_text().splitlines()]
    assert len(lines) == 32
    assert {line["file"] for line in lines} == {path.name for path in paths}
    assert verify_run_journal(tmp_path) == []


def test_truncated_journal_is_reported(tmp_path) -> None:
    write_run_record(_record(), tmp_path)
    with (tmp_path / "runs.jsonl").open("ab") as journal:
        journal.write(b'{"runId":')
    assert verify_run_journal(tmp_path) == ["runs.jsonl:2: invalid or truncated JSON"]


def test_disk_full_does_not_leave_a_truncated_record(monkeypatch, tmp_path) -> None:
    def no_space(fd, data):
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr("psv.run_record.os.write", no_space)
    with pytest.raises(OSError, match="disk full"):
        write_run_record(_record(), tmp_path)
    assert list(tmp_path.glob("run-*.json")) == []
    assert not (tmp_path / "runs.jsonl").exists()


def test_zero_byte_write_does_not_leave_a_truncated_record(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("psv.run_record.os.write", lambda fd, data: 0)
    with pytest.raises(OSError, match="failed to write"):
        write_run_record(_record(), tmp_path)
    assert list(tmp_path.glob("run-*.json")) == []


def test_partial_journal_append_is_reported_as_an_os_error(monkeypatch, tmp_path) -> None:
    real_write = __import__("os").write
    calls = 0

    def partial_second_write(fd, data):
        nonlocal calls
        calls += 1
        if calls == 2:
            return max(0, len(data) - 1)
        return real_write(fd, data)

    monkeypatch.setattr("psv.run_record.os.write", partial_second_write)
    with pytest.raises(OSError, match="partial run journal write"):
        write_run_record(_record(), tmp_path)


def test_read_only_log_target_is_a_clean_os_error(monkeypatch, tmp_path) -> None:
    def denied(path, payload):
        raise PermissionError("read-only log directory")

    monkeypatch.setattr("psv.run_record._write_exclusive", denied)
    with pytest.raises(PermissionError, match="read-only"):
        write_run_record(_record(), tmp_path)
    assert not (tmp_path / "runs.jsonl").exists()


def test_refuses_invalid_checksum(tmp_path) -> None:
    rec = _record()
    rec["exitCode"] = 0
    with pytest.raises(ValueError, match="invalid checksum"):
        write_run_record(rec, tmp_path)


def test_record_requires_ordered_aware_timestamps() -> None:
    aware = datetime(2026, 7, 9, tzinfo=UTC)
    naive = datetime(2026, 7, 9)
    with pytest.raises(ValueError, match="timezone-aware"):
        build_run_record(
            command="reconcile",
            inputs={},
            report=None,
            exit_code=2,
            started_at=naive,
            finished_at=aware,
        )
    with pytest.raises(ValueError, match="must not precede"):
        build_run_record(
            command="reconcile",
            inputs={},
            report=None,
            exit_code=2,
            started_at=aware,
            finished_at=aware.replace(year=2025),
        )


@pytest.mark.parametrize(
    ("command", "exit_code"), [(" ", 0), ("reconcile", True), ("reconcile", -1)]
)
def test_record_rejects_invalid_command_and_exit_code(command: str, exit_code: object) -> None:
    now = datetime(2026, 7, 9, tzinfo=UTC)
    with pytest.raises(ValueError):
        build_run_record(
            command=command,
            inputs={},
            report=None,
            exit_code=exit_code,  # type: ignore[arg-type]
            started_at=now,
            finished_at=now,
        )


def test_journal_verifier_reports_missing_journal(tmp_path) -> None:
    assert verify_run_journal(tmp_path) == ["runs.jsonl is missing"]


@pytest.mark.parametrize(
    ("journal_entry", "record_payload", "expected"),
    [
        ([], None, "entry must be an object"),
        ({"file": "../escape.json"}, None, "unsafe record filename"),
        ({"file": "missing.json"}, None, "record is missing or invalid"),
        ({"file": "record.json"}, {"runId": "sha256:bad"}, "record checksum is invalid"),
    ],
)
def test_journal_verifier_reports_structural_corruption(
    tmp_path, journal_entry: object, record_payload: object, expected: str
) -> None:
    (tmp_path / "runs.jsonl").write_text(json.dumps(journal_entry) + "\n", encoding="utf-8")
    if record_payload is not None:
        (tmp_path / "record.json").write_text(json.dumps(record_payload), encoding="utf-8")
    assert expected in verify_run_journal(tmp_path)[0]


def test_journal_verifier_reports_index_record_run_id_mismatch(tmp_path) -> None:
    record_path = write_run_record(_record(), tmp_path)
    entry = json.loads((tmp_path / "runs.jsonl").read_text(encoding="utf-8"))
    entry["runId"] = "sha256:" + "0" * 64
    (tmp_path / "runs.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")
    assert verify_run_record(json.loads(record_path.read_text(encoding="utf-8")))
    assert "journal/record runId mismatch" in verify_run_journal(tmp_path)[0]


def test_record_conforms_to_checked_in_schema() -> None:
    from jsonschema import Draft202012Validator

    schema_path = Path(__file__).parents[1] / "schemas" / "run-record-v1.1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(_record())


def _cli_argv(*extra: str, paid: bool = True) -> list[str]:
    return [
        "reconcile",
        "--rail",
        "mock-anvil",
        "--payer",
        PAYER,
        "--payee",
        PAYEE,
        "--nonce",
        NONCE,
        "--tx-hash",
        TX_HASH,
        "--log-index",
        "0",
        "--required-amount",
        "100",
        "--payer-before",
        "1000",
        "--payee-before",
        "0",
        "--sut-paid" if paid else "--sut-unpaid",
        *extra,
    ]


def test_cli_log_dir_writes_record(monkeypatch: Any, tmp_path) -> None:
    token = strict_token(get_rail("mock-anvil"), receipt_status=0, payer_after=1000, payee_after=0)
    monkeypatch.setattr("psv.cli.token_for_rail", lambda rail, rpc: token)
    code = main(_cli_argv("--log-dir", str(tmp_path)))
    assert code == 1  # phantom credit
    records = list(tmp_path.glob("run-*.json"))
    assert len(records) == 1
    assert (tmp_path / "runs.jsonl").exists()
    assert verify_run_record(json.loads(records[0].read_text())) is True


def test_cli_logging_on_by_default(monkeypatch: Any, tmp_path) -> None:
    token = strict_token(get_rail("mock-anvil"))
    monkeypatch.setattr("psv.cli.token_for_rail", lambda rail, rpc: token)
    monkeypatch.delenv("PSV_NO_LOG", raising=False)
    monkeypatch.chdir(tmp_path)
    code = main(_cli_argv())
    assert code == 0  # consistent_paid
    assert (tmp_path / "psv-runs" / "runs.jsonl").exists()
    assert list((tmp_path / "psv-runs").glob("run-*.json"))


def test_cli_no_log_suppresses(monkeypatch: Any, tmp_path) -> None:
    token = strict_token(get_rail("mock-anvil"))
    monkeypatch.setattr("psv.cli.token_for_rail", lambda rail, rpc: token)
    monkeypatch.delenv("PSV_NO_LOG", raising=False)
    monkeypatch.chdir(tmp_path)
    code = main(_cli_argv("--no-log"))
    assert code == 0
    assert not (tmp_path / "psv-runs").exists()


def test_cli_rpc_error_is_logged(monkeypatch: Any, tmp_path) -> None:
    from psv.anvil import RpcError

    def boom(rail, rpc):
        raise RpcError("no route to chain")

    monkeypatch.setattr("psv.cli.token_for_rail", boom)
    code = main(_cli_argv("--log-dir", str(tmp_path)))
    assert code == 2
    records = list(tmp_path.glob("run-*.json"))
    assert len(records) == 1
    rec = json.loads(records[0].read_text())
    assert rec["error"].startswith("reconciliation error")
    assert rec["exitCode"] == 2
    assert rec["consistent"] is False
    assert rec["report"] is None
    assert verify_run_record(rec) is True


def test_cli_parse_error_is_not_recorded_as_an_executed_run(tmp_path, capsys) -> None:
    # A malformed address must not escape as a traceback — it's a clean exit 2,
    # Parsing fails before execution, so no run record is created.
    argv = _cli_argv("--log-dir", str(tmp_path))
    argv[argv.index("--payer") + 1] = "0xNOTHEX"
    code = main(argv)
    assert code == 2
    assert list(tmp_path.glob("run-*.json")) == []
    assert "error" in capsys.readouterr().err.lower()
