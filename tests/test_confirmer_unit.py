"""Offline regression tests for transaction-bound settlement confirmation."""

from __future__ import annotations

from typing import Any

import pytest

from psv.chain import TOPIC_TRANSFER, TOPIC_TRANSFER_V2
from psv.reference_sut.confirmer import (
    TOPIC_AUTHORIZATION_USED,
    EventWatchingConfirmer,
    topic_addr,
    topic_nonce,
)

TOKEN = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
PAYER = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
PAYEE = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
TX = "0x" + "aa" * 32
OTHER_TX = "0x" + "bb" * 32
NONCE = "0x" + "cc" * 32
OTHER_NONCE = "0x" + "dd" * 32
BLOCK = 101


def _transfer_log(
    *,
    tx_hash: str = TX,
    log_index: int = 1,
    value: int = 10_000,
    topic0: str = TOPIC_TRANSFER,
) -> dict[str, object]:
    return {
        "address": TOKEN,
        "topics": [topic0, topic_addr(PAYER), topic_addr(PAYEE)],
        "data": hex(value),
        "transactionHash": tx_hash,
        "blockNumber": hex(BLOCK),
        "logIndex": hex(log_index),
    }


def _authorization_log(*, nonce: str = NONCE, tx_hash: str = TX) -> dict[str, object]:
    return {
        "address": TOKEN,
        "topics": [TOPIC_AUTHORIZATION_USED, topic_addr(PAYER), topic_nonce(nonce)],
        "data": "0x",
        "transactionHash": tx_hash,
        "blockNumber": hex(BLOCK),
        "logIndex": "0x0",
    }


def _receipt(
    *,
    tx_hash: str = TX,
    status: int = 1,
    transfer: dict[str, object] | None = None,
    authorization: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "status": hex(status),
        "transactionHash": tx_hash,
        "to": TOKEN,
        "blockNumber": hex(BLOCK),
        "logs": [authorization or _authorization_log(), transfer or _transfer_log()],
    }


def _confirm(
    receipt: dict[str, object] | None,
    *,
    submitted_tx: str = TX,
    nonce: str = NONCE,
    expected_value: int = 10_000,
    fetched: list[dict[str, Any]] | None = None,
    watched_topic0: str = TOPIC_TRANSFER,
    from_block: int = 100,
) -> bool:
    logs = [_transfer_log()] if fetched is None else fetched
    confirmer = EventWatchingConfirmer(
        fetch_logs=lambda address, topics, block: logs,
        watched_topic0=watched_topic0,
    )
    return confirmer.is_settled(
        token=TOKEN,
        payer=PAYER,
        payee=PAYEE,
        expected_value=expected_value,
        authorization_nonce=nonce,
        submitted_tx=submitted_tx,
        receipt=receipt,
        from_block=from_block,
    )


def test_confirms_exact_submitted_authorization() -> None:
    assert _confirm(_receipt())


def test_blind_after_event_drift() -> None:
    drifted = _transfer_log(topic0=TOPIC_TRANSFER_V2)
    assert not _confirm(_receipt(transfer=drifted), fetched=[])


def test_reverted_or_missing_receipt_never_confirms() -> None:
    assert not _confirm(None)
    assert not _confirm(_receipt(status=0))


def test_integer_rpc_quantities_are_accepted_without_coercion() -> None:
    receipt = _receipt()
    receipt["status"] = 1
    receipt["blockNumber"] = BLOCK
    assert _confirm(receipt)


def test_underpayment_and_overpayment_do_not_match_exact_policy() -> None:
    assert not _confirm(_receipt(transfer=_transfer_log(value=9_999)), fetched=[])
    assert not _confirm(_receipt(transfer=_transfer_log(value=10_001)), fetched=[])


def test_other_transaction_cannot_settle_order_even_in_same_block() -> None:
    old_transfer = _transfer_log(tx_hash=OTHER_TX)
    old_authorization = _authorization_log(tx_hash=OTHER_TX)
    receipt = _receipt(tx_hash=OTHER_TX, transfer=old_transfer, authorization=old_authorization)
    assert not _confirm(receipt, submitted_tx=TX, fetched=[old_transfer])


def test_other_order_nonce_cannot_replay_same_transfer() -> None:
    receipt = _receipt(authorization=_authorization_log(nonce=OTHER_NONCE))
    assert not _confirm(receipt, nonce=NONCE)


def test_log_must_be_the_same_receipt_log_index_returned_by_rpc() -> None:
    receipt_transfer = _transfer_log(log_index=1)
    unrelated_fetched = _transfer_log(log_index=8)
    assert not _confirm(_receipt(transfer=receipt_transfer), fetched=[unrelated_fetched])


def test_receipt_contract_and_quote_block_are_enforced() -> None:
    wrong_contract = _receipt()
    wrong_contract["to"] = PAYEE
    assert not _confirm(wrong_contract)
    assert not _confirm(_receipt(), from_block=BLOCK + 1)


@pytest.mark.parametrize("block_number", [None, "not-hex"])
def test_malformed_receipt_block_number_never_confirms(block_number: object) -> None:
    receipt = _receipt()
    receipt["blockNumber"] = block_number
    assert not _confirm(receipt)


def test_receipt_logs_must_be_a_list() -> None:
    receipt = _receipt()
    receipt["logs"] = {"not": "a list"}
    assert not _confirm(receipt)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("removed", True),
        ("address", PAYEE),
        ("transactionHash", OTHER_TX),
        ("blockNumber", hex(BLOCK + 1)),
        ("logIndex", "not-hex"),
    ],
)
def test_malformed_or_unrelated_receipt_logs_are_ignored(field: str, value: object) -> None:
    transfer = _transfer_log()
    transfer[field] = value
    assert not _confirm(_receipt(transfer=transfer))


@pytest.mark.parametrize(
    "fetched",
    [
        [{"transactionHash": TX}],
        [{"transactionHash": TX, "logIndex": "not-hex"}],
        [{"transactionHash": 7, "logIndex": "0x1"}],
    ],
)
def test_fetched_log_requires_valid_transaction_identity(
    fetched: list[dict[str, object]],
) -> None:
    assert not _confirm(_receipt(), fetched=fetched)
