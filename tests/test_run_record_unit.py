"""Tests for psv structured, tamper-evident run records."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from psv.cli import main
from psv.rails import get_rail, token_for_rail
from psv.run_record import (
    _clean_inputs,
    _redact_url,
    build_run_record,
    verify_run_record,
    write_run_record,
)

PAYER = "0x" + "11" * 20
PAYEE = "0x" + "22" * 20
NONCE = "0x" + "ab" * 32
_SEL_BALANCE_OF = "70a08231"
_SEL_AUTH_STATE = "e94a0102"


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


def _fake_token(balances: dict[str, int], nonce_used: bool):
    def transport(req: dict[str, Any]) -> dict[str, Any]:
        result = "0x0"
        if req["method"] == "eth_call":
            data = req["params"][0]["data"]
            selector = data[2:10]
            if selector == _SEL_BALANCE_OF:
                who = ("0x" + data[-40:]).lower()
                result = hex(balances.get(who, 0))
            elif selector == _SEL_AUTH_STATE:
                result = hex(1 if nonce_used else 0)
        return {"jsonrpc": "2.0", "id": req["id"], "result": result}

    from psv.anvil import RpcClient

    return token_for_rail(get_rail("eurc-base"), RpcClient(transport=transport))


def test_cli_log_dir_writes_record(monkeypatch: Any, tmp_path) -> None:
    token = _fake_token({PAYER.lower(): 1000, PAYEE.lower(): 0}, nonce_used=False)
    monkeypatch.setattr("psv.cli.token_for_rail", lambda rail, rpc: token)
    code = main(
        [
            "reconcile",
            "--rail",
            "eurc-base",
            "--payer",
            PAYER,
            "--payee",
            PAYEE,
            "--nonce",
            NONCE,
            "--payer-before",
            "1000",
            "--payee-before",
            "0",
            "--sut-paid",
            "--log-dir",
            str(tmp_path),
        ]
    )
    assert code == 1  # phantom credit
    records = list(tmp_path.glob("run-*.json"))
    assert len(records) == 1
    assert (tmp_path / "runs.jsonl").exists()
    assert verify_run_record(json.loads(records[0].read_text())) is True
