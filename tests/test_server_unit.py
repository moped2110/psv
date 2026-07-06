"""Offline unit tests for the reference SUT's domain logic (no chain).

A fake RPC stands in for Anvil, so we can exercise quoting, the G3 guards, status,
ledger backup/restore and reconciliation without settling anything on-chain.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

pytest.importorskip("eth_account")

from psv.reconciliation import TOPIC_TRANSFER, topic_addr
from psv.reference_sut.server import ReferenceSut, SutConfig, _Order

DEPLOYER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TOKEN = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
MERCHANT = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
PAYER = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


class FakeRpc:
    def __init__(self, block: int = 100, logs: list[dict[str, Any]] | None = None) -> None:
        self._block = block
        self._logs = logs or []

    def block_number(self) -> int:
        return self._block

    def get_logs(self, *, address: str, topics: list[Any], from_block: Any) -> list[dict[str, Any]]:
        return list(self._logs)


def make_sut(*, logs: list[dict[str, Any]] | None = None, **cfg: Any) -> ReferenceSut:
    sut = ReferenceSut(
        SutConfig(
            token_address=TOKEN, merchant_address=MERCHANT, facilitator_key=DEPLOYER_KEY, **cfg
        )
    )
    sut.rpc = FakeRpc(block=100, logs=logs)  # type: ignore[assignment]
    return sut


def _transfer(value: int, tx: str, payer: str = PAYER) -> dict[str, Any]:
    return {
        "topics": [TOPIC_TRANSFER, topic_addr(payer), topic_addr(MERCHANT)],
        "data": hex(value),
        "transactionHash": tx,
    }


def test_quote_shape_and_storage() -> None:
    sut = make_sut()
    q = sut.quote()
    assert q["order_id"].startswith("ord_")
    assert q["amount"] == "10000"
    assert q["payTo"] == MERCHANT
    assert q["network"] == "eip155:84532"
    assert q["extra"] == {"name": "USDC", "version": "2"}
    assert q["expires_at"] > int(time.time())
    assert q["order_id"] in sut.orders


def test_pay_unknown_order() -> None:
    sut = make_sut()
    r = sut.pay("ord_nope", {})
    assert r["settled"] is False and r["reason"] == "unknown_order"


def test_pay_rejects_expired_quote() -> None:
    sut = make_sut()
    sut.orders["ord_old"] = _Order(
        order_id="ord_old", amount=10_000, expires_at=int(time.time()) - 1, quoted_fair_price=10_000
    )
    r = sut.pay("ord_old", {})
    assert r["settled"] is False and r["reason"] == "quote_expired"


def test_pay_rejects_stale_quote_when_repricing() -> None:
    sut = make_sut(reprice_on_pay=True, reprice_tolerance=0.02)
    q = sut.quote()
    sut.fair_price = 30_000  # fair value tripled after the quote locked 10_000
    r = sut.pay(q["order_id"], {})
    assert r["settled"] is False and r["reason"] == "stale_quote"
    assert r.get("submitted_tx") is None  # never reached settlement


def test_status_known_and_unknown() -> None:
    sut = make_sut()
    assert sut.status("ord_nope")["known"] is False
    q = sut.quote()
    st = sut.status(q["order_id"])
    assert st["known"] is True and st["paid"] is False and st["resource"] is None


def test_backup_restore_drops_later_orders() -> None:
    sut = make_sut()
    first = sut.quote()["order_id"]
    backup = sut.backup_ledger()
    later = sut.quote()["order_id"]
    assert later in sut.orders
    sut.restore_ledger(backup)
    assert first in sut.orders
    assert later not in sut.orders  # booked after the backup -> lost on restore


def test_reconcile_reports_gap_without_healing_when_disabled() -> None:
    sut = make_sut(logs=[_transfer(10_000, "0xAA"), _transfer(25_000, "0xBB")])
    gap = sut.reconcile(from_block=0)
    assert {c.tx_hash for c in gap} == {"0xaa", "0xbb"}
    assert not any(o.recovered for o in sut.orders.values())  # disabled -> no heal


def test_reconcile_heals_when_enabled() -> None:
    sut = make_sut(reconciliation_enabled=True, logs=[_transfer(25_000, "0xBB")])
    gap = sut.reconcile(from_block=0)
    assert len(gap) == 1
    recovered = [o for o in sut.orders.values() if o.recovered]
    assert recovered and recovered[0].paid and recovered[0].submitted_tx == "0xbb"
    # a second pass has nothing left to reconcile
    assert sut.reconcile(from_block=0) == []
