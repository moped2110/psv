"""Offline unit tests for the reconciliation logic (D3). No chain.

We hand-build Transfer logs and a ledger's known tx set, and prove the diff
surfaces exactly the on-chain credits the ledger forgot.
"""

from __future__ import annotations

from psv.reconciliation import (
    TOPIC_TRANSFER,
    decode_transfer_log,
    find_unreconciled,
    topic_addr,
)

PAYER = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
MERCHANT = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"


def _transfer(value: int, tx: str) -> dict[str, object]:
    return {
        "topics": [TOPIC_TRANSFER, topic_addr(PAYER), topic_addr(MERCHANT)],
        "data": hex(value),
        "transactionHash": tx,
    }


def test_decode_transfer_log() -> None:
    credit = decode_transfer_log(_transfer(10_000, "0xabc"))
    assert credit.value == 10_000
    assert credit.payer.lower() == PAYER.lower()
    assert credit.tx_hash == "0xabc"


def test_all_reconciled_when_ledger_complete() -> None:
    transfers = [_transfer(10_000, "0xAA"), _transfer(10_000, "0xBB")]
    known = {"0xaa", "0xbb"}
    assert find_unreconciled(transfers, known) == []


def test_restore_loses_a_payment_surfaced_by_reconcile() -> None:
    # Chain has two settlements; the ledger (after a restore) only knows the first.
    transfers = [_transfer(10_000, "0xAA"), _transfer(25_000, "0xBB")]
    known = {"0xaa"}  # 0xBB booked after the backup, lost on restore
    gap = find_unreconciled(transfers, known)
    assert len(gap) == 1
    assert gap[0].tx_hash == "0xbb"
    assert gap[0].value == 25_000


def test_case_insensitive_tx_match() -> None:
    transfers = [_transfer(1, "0xDeAdBeEf")]
    assert find_unreconciled(transfers, {"0xdeadbeef"}) == []
