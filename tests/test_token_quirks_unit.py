"""Offline tests for token quirks (decimals + fee-on-transfer)."""

from __future__ import annotations

import pytest

from psv.reconciliation import topic_addr
from psv.reference_sut.confirmer import (
    TOPIC_AUTHORIZATION_USED,
    TOPIC_TRANSFER,
    EventWatchingConfirmer,
    topic_nonce,
)
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
    with pytest.raises(ValueError):
        to_atomic("0.0000000000001", 6)  # fractional magnitude is entirely below one atom


@pytest.mark.parametrize(
    "human",
    [True, 1.5, "not-a-number", "NaN", "Infinity", "-1", "1" * 1025],
)
def test_to_atomic_rejects_non_decimal_or_unbounded_inputs(human: object) -> None:
    with pytest.raises(ValueError):
        to_atomic(human, 6)  # type: ignore[arg-type]


def test_to_atomic_rejects_uint256_overflow_before_huge_scaling() -> None:
    with pytest.raises(ValueError, match="uint256"):
        to_atomic("1e1000", 18)


def test_negative_zero_is_normalized_without_becoming_negative() -> None:
    assert to_atomic("-0", 6) == 0


def test_negative_decimals_rejected() -> None:
    # A nonsensical decimals value must fail loudly, not silently produce garbage.
    with pytest.raises(ValueError):
        to_atomic("1", -1)
    with pytest.raises(ValueError):
        from_atomic(1, -1)


@pytest.mark.parametrize("atomic", [-1, True, 2**256])
def test_from_atomic_rejects_values_outside_uint256(atomic: int) -> None:
    with pytest.raises(ValueError, match="uint256"):
        from_atomic(atomic, 6)


def test_zero_decimal_token_has_canonical_integer_rendering() -> None:
    assert from_atomic(42, 0) == "42"


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

    token = "0x" + "aa" * 20
    tx_hash = "0x" + "bb" * 32
    nonce = "0x" + "cc" * 32
    transfer = {
        "address": token,
        "topics": [TOPIC_TRANSFER, topic_addr(PAYER), topic_addr(MERCHANT)],
        "data": hex(required),
        "transactionHash": tx_hash,
        "blockNumber": "0x1",
        "logIndex": "0x1",
    }

    def fetch(addr, topics, from_block):
        return [transfer]  # gross event, not the net credited

    receipt = {
        "status": "0x1",
        "transactionHash": tx_hash,
        "to": token,
        "blockNumber": "0x1",
        "logs": [
            {
                "address": token,
                "topics": [TOPIC_AUTHORIZATION_USED, topic_addr(PAYER), topic_nonce(nonce)],
                "data": "0x",
                "transactionHash": tx_hash,
                "blockNumber": "0x1",
                "logIndex": "0x0",
            },
            transfer,
        ],
    }

    confirmer = EventWatchingConfirmer(fetch_logs=fetch)
    assert (
        confirmer.is_settled(
            token=token,
            payer=PAYER,
            payee=MERCHANT,
            expected_value=required,
            authorization_nonce=nonce,
            submitted_tx=tx_hash,
            receipt=receipt,
        )
        is True
    )

    # ...but verifying on the real received balance delta catches the underpayment.
    assert received_is_sufficient(net, required) is False
    assert underpayment(net, required) == 200
