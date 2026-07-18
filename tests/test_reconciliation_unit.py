"""Exact settlement identity and strict Transfer-log reconciliation tests."""

from __future__ import annotations

import copy

import pytest
from reconcile_fakes import PAYEE, PAYER, TX_HASH, transfer_log

from psv.reconciliation import (
    TOPIC_TRANSFER,
    ReconciliationError,
    SettlementIdentity,
    decode_transfer_log,
    find_unreconciled,
)

CHAIN_ID = 84532
TOKEN = "0x" + "44" * 20


def _log(value: int = 100, tx_hash: str = TX_HASH, log_index: int = 0):  # type: ignore[no-untyped-def]
    return transfer_log(token=TOKEN, value=value, tx_hash=tx_hash, log_index=log_index)


def test_decode_transfer_log_retains_full_identity_and_provenance() -> None:
    credit = decode_transfer_log(_log(), chain_id=CHAIN_ID)
    assert credit.identity == SettlementIdentity(CHAIN_ID, TOKEN, TX_HASH, 0)
    assert credit.payer == PAYER and credit.payee == PAYEE
    assert credit.value == 100 and credit.block_number == 10
    assert credit.block_hash == "0x" + "10" * 32 and credit.removed is False


def test_multi_log_transaction_uses_log_index_not_only_tx_hash() -> None:
    first = decode_transfer_log(_log(100, log_index=0), chain_id=CHAIN_ID)
    second_log = _log(200, log_index=2)
    gap = find_unreconciled(
        [_log(100, log_index=0), second_log],
        {first.identity},
        chain_id=CHAIN_ID,
        expected_asset=TOKEN,
        expected_payee=PAYEE,
    )
    assert [(credit.tx_hash, credit.log_index, credit.value) for credit in gap] == [
        (TX_HASH, 2, 200)
    ]


def test_duplicate_identical_rpc_logs_are_deduplicated() -> None:
    raw = _log()
    assert len(find_unreconciled([raw, copy.deepcopy(raw)], set(), chain_id=CHAIN_ID)) == 1


def test_conflicting_duplicate_is_inconclusive() -> None:
    original = _log()
    conflicting = _log(101)
    with pytest.raises(ReconciliationError, match="conflicting observations"):
        find_unreconciled([original, conflicting], set(), chain_id=CHAIN_ID)


def test_removed_log_is_persisted_but_not_an_unreconciled_credit() -> None:
    raw = _log()
    raw["removed"] = True
    decoded = decode_transfer_log(raw, chain_id=CHAIN_ID)
    assert decoded.removed is True
    assert find_unreconciled([raw], set(), chain_id=CHAIN_ID) == []


def test_cross_asset_and_cross_recipient_matching_is_rejected() -> None:
    with pytest.raises(ReconciliationError, match="cross-asset"):
        find_unreconciled([_log()], set(), chain_id=CHAIN_ID, expected_asset="0x" + "55" * 20)
    with pytest.raises(ReconciliationError, match="cross-recipient"):
        find_unreconciled([_log()], set(), chain_id=CHAIN_ID, expected_payee="0x" + "66" * 20)


@pytest.mark.parametrize(
    "mutation",
    [
        {"topics": [TOPIC_TRANSFER]},
        {"topics": ["0x" + "00" * 32, "0x" + "00" * 32, "0x" + "00" * 32]},
        {"data": "0x01"},
        {"transactionHash": "0xdeadbeef"},
        {"address": "0x1"},
        {"logIndex": "0x00"},
        {"blockHash": "0x1"},
        {"removed": "false"},
    ],
)
def test_malformed_log_schema_is_rejected(mutation: dict[str, object]) -> None:
    raw = _log()
    raw.update(mutation)
    with pytest.raises(ReconciliationError):
        decode_transfer_log(raw, chain_id=CHAIN_ID)


def test_hash_prefix_collision_cannot_match_another_settlement() -> None:
    tx_a = "0xdeadbeef" + "00" * 28
    tx_b = "0xdeadbeef" + "11" * 28
    known = SettlementIdentity(CHAIN_ID, TOKEN, tx_a, 0)
    gap = find_unreconciled([_log(tx_hash=tx_b)], {known}, chain_id=CHAIN_ID)
    assert len(gap) == 1 and gap[0].tx_hash == tx_b
