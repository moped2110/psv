"""Offline test: the settlement-delay flag controls whether the SUT waits for the
settlement tx to be mined before confirming."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("eth_account")

from psv.reference_sut.server import ReferenceSut, SutConfig

DEPLOYER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TOKEN = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
MERCHANT = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
PAYER = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
AUTH = {"from": PAYER, "to": MERCHANT, "value": "10000", "validAfter": "0",
        "validBefore": str(2**48), "nonce": "0x" + "ab" * 32, "signature": "0x" + "11" * 65}


class _Rpc:
    def __init__(self) -> None:
        self.waited: list[str] = []

    def block_number(self) -> int:
        return 100

    def wait_for_receipt(self, tx_hash: str, **kw: Any) -> dict[str, Any]:
        self.waited.append(tx_hash)
        return {"status": "0x1"}


def _sut(*, confirm_without_waiting: bool) -> tuple[ReferenceSut, _Rpc]:
    sut = ReferenceSut(SutConfig(token_address=TOKEN, merchant_address=MERCHANT,
                                 facilitator_key=DEPLOYER_KEY,
                                 confirm_without_waiting=confirm_without_waiting))
    rpc = _Rpc()
    sut.rpc = rpc  # type: ignore[assignment]
    sut._submit_settlement = lambda auth: "0xtx"  # type: ignore[assignment]
    sut.confirmer.is_settled = lambda **kw: True  # type: ignore[assignment]
    return sut, rpc


def test_default_waits_for_receipt() -> None:
    sut, rpc = _sut(confirm_without_waiting=False)
    oid = sut.quote()["order_id"]
    sut.pay(oid, AUTH)
    assert rpc.waited == ["0xtx"]  # waited for inclusion before confirming


def test_vulnerable_mode_skips_the_wait() -> None:
    sut, rpc = _sut(confirm_without_waiting=True)
    oid = sut.quote()["order_id"]
    sut.pay(oid, AUTH)
    assert rpc.waited == []  # checked immediately, no wait
