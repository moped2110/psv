"""Offline tests for I-class idempotency (no chain).

The chain-touching parts of ``pay`` are stubbed so we can count how many times
the SUT submits a settlement when the same order is paid twice.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("eth_account")

from psv.reference_sut.server import ReferenceSut, SutConfig

DEPLOYER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TOKEN = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
MERCHANT = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
PAYER = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

AUTH = {
    "from": PAYER, "to": MERCHANT, "value": "10000",
    "validAfter": "0", "validBefore": str(2**48),
    "nonce": "0x" + "ab" * 32, "signature": "0x" + "11" * 65,
}


class _Rpc:
    def block_number(self) -> int:
        return 100

    def wait_for_receipt(self, tx_hash: str, **kw: Any) -> dict[str, Any]:
        return {"status": "0x1"}


def make_settling_sut(*, idempotent_pay: bool) -> tuple[ReferenceSut, list[Any]]:
    sut = ReferenceSut(
        SutConfig(token_address=TOKEN, merchant_address=MERCHANT,
                  facilitator_key=DEPLOYER_KEY, idempotent_pay=idempotent_pay)
    )
    submits: list[Any] = []
    sut.rpc = _Rpc()  # type: ignore[assignment]
    sut._submit_settlement = lambda auth: submits.append(auth) or f"0xtx{len(submits)}"  # type: ignore[assignment,func-returns-value]
    sut.confirmer.is_settled = lambda **kw: True  # type: ignore[assignment]
    return sut, submits


def test_vulnerable_sut_resubmits_on_repay() -> None:
    sut, submits = make_settling_sut(idempotent_pay=False)
    oid = sut.quote()["order_id"]
    assert sut.pay(oid, AUTH)["settled"] is True
    sut.pay(oid, AUTH)  # retry / double-click
    assert len(submits) == 2  # re-submitted on-chain a second time
    assert sut.orders[oid].settle_attempts == 2


def test_idempotent_sut_short_circuits_repay() -> None:
    sut, submits = make_settling_sut(idempotent_pay=True)
    oid = sut.quote()["order_id"]
    first = sut.pay(oid, AUTH)
    assert first["settled"] is True and len(submits) == 1
    second = sut.pay(oid, AUTH)  # retry
    assert second["settled"] is True and second.get("idempotent") is True
    assert second["submitted_tx"] == first["submitted_tx"]
    assert len(submits) == 1  # NO second submission
    assert sut.orders[oid].settle_attempts == 1
