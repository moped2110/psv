"""Offline tests for I-class idempotency (no chain).

The chain-touching parts of ``pay`` are stubbed so we can count how many times
the SUT submits a settlement when the same order is paid twice, including the
retry after a ``settlement_pending`` answer (PSV-I-002).
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("eth_account")

from psv.anvil import RpcError
from psv.reference_sut.server import ReferenceSut, SutConfig
from psv.sut import PayOutcome, parse_pay

DEPLOYER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TOKEN = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
MERCHANT = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
PAYER = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

AUTH = {
    "from": PAYER,
    "to": MERCHANT,
    "value": "10000",
    "validAfter": "0",
    "validBefore": str(2**48),
    "nonce": "0x" + "ab" * 32,
    "signature": "0x" + "11" * 65,
}


class _Rpc:
    def block_number(self) -> int:
        return 100

    def wait_for_receipt(self, tx_hash: str, **kw: Any) -> dict[str, Any]:
        return {"status": "0x1"}


def make_settling_sut(*, idempotent_pay: bool) -> tuple[ReferenceSut, list[Any]]:
    sut = ReferenceSut(
        SutConfig(
            token_address=TOKEN,
            merchant_address=MERCHANT,
            facilitator_key=DEPLOYER_KEY,
            idempotent_pay=idempotent_pay,
        )
    )
    submits: list[Any] = []
    sut.rpc = _Rpc()  # type: ignore[assignment]
    sut._submit_settlement = lambda auth: submits.append(auth) or "0x" + f"{len(submits):064x}"  # type: ignore[assignment,func-returns-value]
    sut.confirmer.settlement_log_index = lambda **kw: 1  # type: ignore[assignment]
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


class _PendingRpc:
    """Receipt polling fails until ``mined`` is set: a broadcast whose confirmation
    cannot be established yet, the trigger for x402's ``settlement_pending``."""

    def __init__(self) -> None:
        self.mined = False
        self.waited: list[tuple[str, int]] = []

    def block_number(self) -> int:
        return 100

    def wait_for_receipt(self, tx_hash: str, *, tries: int = 50, **kw: Any) -> dict[str, Any]:
        self.waited.append((tx_hash, tries))
        if not self.mined:
            raise RpcError(f"no receipt for {tx_hash} after {tries} tries")
        return {"status": "0x1"}


def make_pending_sut(*, idempotent_pay: bool) -> tuple[ReferenceSut, list[Any], _PendingRpc]:
    sut = ReferenceSut(
        SutConfig(
            token_address=TOKEN,
            merchant_address=MERCHANT,
            facilitator_key=DEPLOYER_KEY,
            idempotent_pay=idempotent_pay,
            receipt_tries=3,
        )
    )
    submits: list[Any] = []
    rpc = _PendingRpc()
    sut.rpc = rpc  # type: ignore[assignment]
    sut._submit_settlement = lambda auth: submits.append(auth) or "0x" + f"{len(submits):064x}"  # type: ignore[assignment,func-returns-value]
    sut.confirmer.settlement_log_index = lambda **kw: 1  # type: ignore[assignment]
    return sut, submits, rpc


def test_unconfirmed_broadcast_answers_settlement_pending_with_its_tx() -> None:
    sut, submits, rpc = make_pending_sut(idempotent_pay=True)
    oid = sut.quote()["order_id"]
    answer = sut.pay(oid, AUTH)
    result = parse_pay(answer, expected_order_id=oid)
    assert result.outcome is PayOutcome.PENDING
    assert answer["reason"] == "settlement_pending"
    assert result.submitted_tx == "0x" + f"{1:064x}"
    assert rpc.waited == [(result.submitted_tx, 3)]  # receipt_tries is honoured
    assert sut.orders[oid].pending_tx == result.submitted_tx
    assert sut.status(oid)["paid"] is False  # pending is not paid


def test_idempotent_retry_after_settlement_pending_waits_on_the_hash() -> None:
    sut, submits, rpc = make_pending_sut(idempotent_pay=True)
    oid = sut.quote()["order_id"]
    first = parse_pay(sut.pay(oid, AUTH), expected_order_id=oid)
    assert first.outcome is PayOutcome.PENDING
    rpc.mined = True
    second = parse_pay(sut.pay(oid, AUTH), expected_order_id=oid)  # the single retry
    assert second.outcome is PayOutcome.SETTLED
    assert second.submitted_tx == first.submitted_tx
    assert len(submits) == 1  # NO second broadcast
    assert sut.orders[oid].settle_attempts == 1
    assert sut.orders[oid].pending_tx is None
    assert sut.status(oid)["paid"] is True


def test_idempotent_retry_still_unconfirmed_stays_pending_without_broadcasting() -> None:
    sut, submits, _rpc = make_pending_sut(idempotent_pay=True)
    oid = sut.quote()["order_id"]
    first = parse_pay(sut.pay(oid, AUTH), expected_order_id=oid)
    second = parse_pay(sut.pay(oid, AUTH), expected_order_id=oid)
    assert second.outcome is PayOutcome.PENDING
    assert second.submitted_tx == first.submitted_tx
    assert len(submits) == 1


def test_idempotent_pending_retry_is_reconciled_even_after_the_quote_expired() -> None:
    sut, submits, rpc = make_pending_sut(idempotent_pay=True)
    oid = sut.quote()["order_id"]
    sut.pay(oid, AUTH)
    sut.orders[oid].expires_at = 0  # the quote expired while the settlement was in flight
    rpc.mined = True
    assert parse_pay(sut.pay(oid, AUTH)).outcome is PayOutcome.SETTLED
    assert len(submits) == 1


def test_idempotent_pending_retry_confirms_against_the_original_authorization() -> None:
    sut, _submits, rpc = make_pending_sut(idempotent_pay=True)
    seen: list[str] = []
    sut.confirmer.settlement_log_index = lambda **kw: seen.append(kw["authorization_nonce"]) or 1  # type: ignore[assignment,func-returns-value]
    oid = sut.quote()["order_id"]
    sut.pay(oid, AUTH)
    rpc.mined = True
    sut.pay(oid, {**AUTH, "nonce": "0x" + "cd" * 32})
    assert seen == [AUTH["nonce"]]


def test_vulnerable_retry_after_settlement_pending_broadcasts_again() -> None:
    sut, submits, rpc = make_pending_sut(idempotent_pay=False)
    oid = sut.quote()["order_id"]
    first = parse_pay(sut.pay(oid, AUTH), expected_order_id=oid)
    assert first.outcome is PayOutcome.PENDING
    rpc.mined = True
    second = parse_pay(sut.pay(oid, AUTH), expected_order_id=oid)
    assert len(submits) == 2  # the retry broadcast the authorization a second time
    assert sut.orders[oid].settle_attempts == 2
    assert second.submitted_tx != first.submitted_tx
