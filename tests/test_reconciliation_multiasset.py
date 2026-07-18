"""Exact asset/recipient attribution cannot cross-match ledger entries."""

from __future__ import annotations

from reconcile_fakes import PAYEE, TX_HASH, transfer_log

from psv.reconciliation import SettlementIdentity, decode_transfer_log, find_unreconciled

CHAIN_ID = 84532
USDC = "0x" + "a0" * 20
EURC = "0x" + "b1" * 20


def test_equal_value_different_asset_are_distinct_credits() -> None:
    a_log = transfer_log(token=USDC, value=1000)
    b_log = transfer_log(token=EURC, value=1000, log_index=1)
    a = decode_transfer_log(a_log, chain_id=CHAIN_ID)
    b = decode_transfer_log(b_log, chain_id=CHAIN_ID)
    assert a.identity != b.identity and a.asset != b.asset


def test_ledger_entry_for_one_asset_cannot_hide_the_other() -> None:
    eurc_log = transfer_log(token=EURC, value=1000, log_index=1)
    known_usdc = SettlementIdentity(CHAIN_ID, USDC, TX_HASH, 1)
    gap = find_unreconciled([eurc_log], {known_usdc}, chain_id=CHAIN_ID)
    assert len(gap) == 1 and gap[0].asset == EURC


def test_recipient_is_part_of_strict_credit_evidence() -> None:
    credit = decode_transfer_log(transfer_log(token=USDC, value=1), chain_id=CHAIN_ID)
    assert credit.payee == PAYEE
