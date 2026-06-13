"""Offline tests for token quirks (decimals + fee-on-transfer)."""

from __future__ import annotations

import pytest

from psv.reconciliation import topic_addr
from psv.reference_sut.confirmer import TOPIC_TRANSFER, EventWatchingConfirmer
from psv.token_quirks import (
    from_atomic,
    net_after_fee,
    received_is_sufficient,
    to_atomic,
    underpayment,
)

PAYER = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
MERCHANT = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"


def test_decimals_roundtrip() -> None:
    assert to_atomic("0.01", 6) == 10_000
    assert from_atomic(10_000, 6) == "0.01"
    assert to_atomic("1", 18) == 10**18


def test_wrong_decimals_assumption_changes_the_amount() -> None:
    # The same "0.01" priced at 6 vs 18 decimals differs by 10^12 — a system that
    # hardcodes 6 decimals against an 18-decimals token under-charges massively.
    assert to_atomic("0.01", 18) == to_atomic("0.01", 6) * 10**12


def test_to_atomic_rejects_unrepresentable() -> None:
    with pytest.raises(ValueError):
        to_atomic("0.0000001", 6)  # 7 dp at 6 decimals -> not representable


def test_net_after_fee() -> None:
    assert net_after_fee(10_000, 0) == 10_000
    assert net_after_fee(10_000, 200) == 9_800  # 2%
    with pytest.raises(ValueError):
        net_after_fee(10_000, 20_000)


def test_received_sufficiency_and_underpayment() -> None:
    assert received_is_sufficient(10_000, 10_000) is True
    assert received_is_sufficient(9_800, 10_000) is False
    assert underpayment(9_800, 10_000) == 200
    assert underpayment(10_000, 10_000) == 0


def test_fee_token_fools_event_watcher_but_not_received_delta() -> None:
    # A deceptive fee token emits a GROSS Transfer event (value = required) while
    # only crediting the net. The event-watching confirmer is fooled...
    required = 10_000
    fee_bps = 200
    net = net_after_fee(required, fee_bps)  # 9_800 actually credited

    def fetch(addr, topics, from_block):
        return [{"topics": [TOPIC_TRANSFER, topic_addr(PAYER), topic_addr(MERCHANT)],
                 "data": hex(required)}]  # gross event, not the net credited

    confirmer = EventWatchingConfirmer(fetch_logs=fetch)
    assert confirmer.is_settled(token="0xt", payer=PAYER, payee=MERCHANT, min_value=required) is True

    # ...but verifying on the real received balance delta catches the underpayment.
    assert received_is_sufficient(net, required) is False
    assert underpayment(net, required) == 200
