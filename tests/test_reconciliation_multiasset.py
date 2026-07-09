"""Multi-asset attribution in reconciliation.

A merchant that accepts more than one token must be able to tell credits apart by
asset. Two payments of equal value from the same payer in different tokens are
distinct on-chain credits, and reconciling one asset's ledger must never mark the
other asset's credit as accounted for.
"""

from __future__ import annotations

from psv.reconciliation import (
    TOPIC_TRANSFER,
    decode_transfer_log,
    find_unreconciled,
    topic_addr,
)

USDC = "0x" + "a0" * 20
EURC = "0x" + "b1" * 20
MERCHANT = "0x" + "11" * 20
PAYER = "0x" + "22" * 20


def _log(asset: str, tx: str, value: int, payer: str = PAYER) -> dict[str, object]:
    return {
        "address": asset,
        "topics": [TOPIC_TRANSFER, topic_addr(payer), topic_addr(MERCHANT)],
        "data": hex(value),
        "transactionHash": tx,
    }


def test_decode_carries_the_asset() -> None:
    credit = decode_transfer_log(_log(USDC, "0x" + "cd" * 32, 1000))
    assert credit.asset == USDC.lower()
    assert credit.value == 1000
    assert credit.payer.lower() == PAYER.lower()


def test_equal_value_different_asset_are_distinct_credits() -> None:
    tx_a, tx_b = "0x" + "a1" * 32, "0x" + "b2" * 32
    a = decode_transfer_log(_log(USDC, tx_a, 1000))
    b = decode_transfer_log(_log(EURC, tx_b, 1000))
    # Same payer and amount, but different asset → not the same record.
    assert a != b
    assert a.asset != b.asset


def test_reconciling_one_asset_leaves_the_other_unreconciled() -> None:
    tx_usdc, tx_eurc = "0x" + "a1" * 32, "0x" + "b2" * 32
    logs = [_log(USDC, tx_usdc, 1000), _log(EURC, tx_eurc, 1000)]
    # The ledger only knows about the USDC settlement.
    unreconciled = find_unreconciled(logs, {tx_usdc})
    # The EURC credit is still an unaccounted-for credit — attributable by asset.
    assert [c.tx_hash for c in unreconciled] == [tx_eurc]
    assert unreconciled[0].asset == EURC.lower()
