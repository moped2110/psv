"""CLI/report contract tests over strict synthetic receipt evidence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
from reconcile_fakes import NONCE, PAYEE, PAYER, TX_HASH, strict_token

from psv.cli import main, run_reconcile
from psv.rails import get_rail
from psv.report import REPORT_VERSION, exit_code, validate_report_document


def _report(*, paid: bool = True, **token_options: object):  # type: ignore[no-untyped-def]
    rail = get_rail("mock-anvil")
    return run_reconcile(
        strict_token(rail, **token_options),
        rail,
        payer=PAYER,
        payee=PAYEE,
        nonce=NONCE,
        transaction_hash=TX_HASH,
        log_index=0,
        required_amount=100,
        payer_before=1000,
        payee_before=0,
        sut_believes_paid=paid,
        generated_at=datetime(2026, 7, 18, 12, tzinfo=UTC),
    )


def _argv(*extra: str, paid: bool = True) -> list[str]:
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


def test_run_reconcile_verdict_and_exit_code() -> None:
    clean = _report()
    assert clean.kind == "consistent_paid" and exit_code(clean) == 0
    silent = _report(paid=False)
    assert silent.kind == "silent_loss" and exit_code(silent) == 1
    underpaid = _report(payer_after=900, payee_after=90, event_value=100)
    assert underpaid.kind == "underpaid_credit" and exit_code(underpaid) == 1


def test_report_v2_is_deterministic_and_contains_full_provenance() -> None:
    report = _report(paid=False)
    doc = json.loads(report.to_json())
    assert doc["reportVersion"] == REPORT_VERSION == "2.0"
    assert doc["generatedAt"] == "2026-07-18T12:00:00+00:00"
    assert doc["divergence"]["reasonCode"] == "PSV-RECON-SILENT-LOSS"
    assert doc["payment"]["requiredAmount"] == 100
    assert doc["payment"]["receivedAmount"] == 100
    assert doc["evidence"]["transaction"]["hash"] == TX_HASH
    assert doc["evidence"]["settlementBlock"]["number"] == 10
    assert doc["privacy"]["policy"]
    validate_report_document(doc)
    assert report.to_json() == report.to_json()


def test_checked_in_json_schema_validates_report() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = files("psv.schemas").joinpath("reconciliation-report-v2.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(_report().to_dict())


def test_report_markdown_contains_amounts_and_chain_evidence() -> None:
    md = _report(payer_after=900, payee_after=90, event_value=100).to_markdown()
    assert "Read-only" in md and "DIVERGENCE" in md
    assert "required 100; received 90" in md
    assert TX_HASH in md and "Settlement block" in md


def test_main_reconcile_end_to_end(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    rail = get_rail("mock-anvil")
    token = strict_token(rail, receipt_status=0, payer_after=1000, payee_after=0)
    monkeypatch.setattr("psv.cli.token_for_rail", lambda selected, rpc: token)
    code = main(_argv())
    captured = capsys.readouterr()
    assert code == 1
    assert "PHANTOM CREDIT" in captured.out
    assert captured.err == ""


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--payer", "0x1"),
        ("--nonce", "0x1"),
        ("--required-amount", "0"),
        ("--required-amount", "-1"),
        ("--payer-before", "-1"),
        ("--rpc-url", "http://example.com:notaport"),
        ("--rpc-timeout", "0"),
    ],
)
def test_invalid_inputs_exit_2_without_traceback(flag: str, value: str, capsys: Any) -> None:
    argv = _argv()
    if flag in argv:
        argv[argv.index(flag) + 1] = value
    else:
        argv.extend([flag, value])
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert "error" in captured.err.lower()
    assert "Traceback" not in captured.err


def test_unknown_rail_exits_2(capsys: Any) -> None:
    argv = _argv()
    argv[argv.index("--rail") + 1] = "nope"
    assert main(argv) == 2
    assert "unknown rail" in capsys.readouterr().err


def test_report_output_failure_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    rail = get_rail("mock-anvil")
    monkeypatch.setattr("psv.cli.token_for_rail", lambda selected, rpc: strict_token(rail))

    def fail_write(self: Path, data: bytes) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", fail_write)
    assert main(_argv("--json", str(tmp_path / "report.json"))) == 2
    captured = capsys.readouterr()
    assert "output error" in captured.err and "Traceback" not in captured.err


def test_audit_record_failure_overrides_clean_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    rail = get_rail("mock-anvil")
    monkeypatch.setattr("psv.cli.token_for_rail", lambda selected, rpc: strict_token(rail))
    monkeypatch.setattr(
        "psv.run_record.write_run_record",
        lambda record, log_dir: (_ for _ in ()).throw(OSError("read-only directory")),
    )
    assert main(_argv("--log-dir", str(tmp_path))) == 2
    captured = capsys.readouterr()
    assert "audit-record error" in captured.err
