"""Offline tests for the read-only `psv reconcile` CLI + report rendering.

No chain: the TokenView runs over an injected fake JSON-RPC transport (same pattern
as test_rails_unit). Verifies the reconcile core, the JSON/Markdown report, the exit
code, and the argparse wiring end-to-end (RPC swapped out via monkeypatch).
"""

from __future__ import annotations

import json
from typing import Any

from psv.anvil import RpcClient
from psv.chain import TokenView
from psv.cli import main, run_reconcile
from psv.rails import get_rail, token_for_rail
from psv.report import ReconReport, exit_code

PAYER = "0x" + "11" * 20
PAYEE = "0x" + "22" * 20
NONCE = "0x" + "ab" * 32
_SEL_BALANCE_OF = "70a08231"
_SEL_AUTH_STATE = "e94a0102"


def _fake_token(balances: dict[str, int], nonce_used: bool) -> TokenView:
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

    return token_for_rail(get_rail("eurc-base"), RpcClient(transport=transport))


def test_run_reconcile_phantom_credit_is_failure() -> None:
    token = _fake_token({PAYER.lower(): 1000, PAYEE.lower(): 0}, nonce_used=False)
    report = run_reconcile(
        token, get_rail("eurc-base"), payer=PAYER, payee=PAYEE, nonce=NONCE,
        payer_before=1000, payee_before=0, sut_believes_paid=True,
    )
    assert report.kind == "phantom_credit"
    assert report.is_failure is True
    assert exit_code(report) == 1


def test_run_reconcile_consistent_paid_is_clean() -> None:
    token = _fake_token({PAYER.lower(): 900, PAYEE.lower(): 100}, nonce_used=True)
    report = run_reconcile(
        token, get_rail("usdc-base"), payer=PAYER, payee=PAYEE, nonce=NONCE,
        payer_before=1000, payee_before=0, sut_believes_paid=True,
    )
    assert report.kind == "consistent_paid"
    assert report.is_failure is False
    assert exit_code(report) == 0


def test_report_json_shape() -> None:
    token = _fake_token({PAYER.lower(): 900, PAYEE.lower(): 100}, nonce_used=True)
    report = run_reconcile(
        token, get_rail("usdc-base"), payer=PAYER, payee=PAYEE, nonce=NONCE,
        payer_before=1000, payee_before=0, sut_believes_paid=False,  # silent loss
    )
    doc = json.loads(report.to_json())
    assert doc["divergence"]["kind"] == "silent_loss"
    assert doc["tool"]["readOnly"] is True
    assert doc["rail"]["key"] == "usdc-base"
    assert doc["payment"]["payer"] == PAYER


def test_report_markdown_is_readonly_and_explains() -> None:
    report = ReconReport(
        rail_key="usdc-base", rail_label="USDC on Base", chain_id=8453,
        token_address="0xabc", payer=PAYER, payee=PAYEE, nonce=NONCE,
        sut_believes_paid=True, kind="phantom_credit", severity="critical",
        message="PHANTOM CREDIT: …", is_failure=True,
    )
    md = report.to_markdown()
    assert "Read-only" in md
    assert "PHANTOM CREDIT" in md
    assert "DIVERGENCE" in md


def test_main_reconcile_end_to_end(monkeypatch: Any) -> None:
    # Swap the live RPC for a fake-transport token; exercise argparse + exit code.
    token = _fake_token({PAYER.lower(): 1000, PAYEE.lower(): 0}, nonce_used=False)
    monkeypatch.setattr("psv.cli.token_for_rail", lambda rail, rpc: token)
    code = main([
        "reconcile", "--rail", "eurc-base", "--payer", PAYER, "--payee", PAYEE,
        "--nonce", NONCE, "--payer-before", "1000", "--payee-before", "0", "--sut-paid",
    ])
    assert code == 1  # phantom credit


def test_main_unknown_rail_exits_2() -> None:
    code = main([
        "reconcile", "--rail", "nope", "--payer", PAYER, "--payee", PAYEE,
        "--nonce", NONCE, "--payer-before", "0", "--payee-before", "0", "--sut-unpaid",
    ])
    assert code == 2
