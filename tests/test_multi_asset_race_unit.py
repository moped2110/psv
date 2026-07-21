"""Unit tests for asset-scoped reconciliation — the multi-asset settlement race (PSV-RD-003).

A single block can carry transfers of several tokens to the same payee. A system that credits
an order denominated in asset A when it sees a same-block transfer of asset B to the payee has
lost the race. The strict ``find_unreconciled`` fails closed on mixed assets; ``reconcile_asset_scoped``
scopes a mixed snapshot to the expected asset so a different-asset transfer can never be
mis-attributed, while a legitimate expected-asset credit still reconciles. Pure, offline.
"""

from __future__ import annotations

import pytest

from psv.reconciliation import (
    TOPIC_TRANSFER,
    ReconciliationError,
    decode_transfer_log,
    find_unreconciled,
    reconcile_asset_scoped,
    topic_addr,
)

_ASSET_A = "0x" + "aa" * 20
_ASSET_B = "0x" + "bb" * 20
_PAYER = "0x" + "11" * 20
_PAYEE = "0x" + "22" * 20
_OTHER = "0x" + "33" * 20
_TX = "0x" + "cc" * 32
_BLOCK = "0x" + "dd" * 32


def _log(*, asset: str, payee: str, value: int, log_index: str, removed: bool = False) -> dict:
    """Build one canonical Transfer log for a same-block multi-asset snapshot."""
    return {
        "topics": [TOPIC_TRANSFER, topic_addr(_PAYER), topic_addr(payee)],
        "data": "0x" + f"{value:064x}",
        "address": asset,
        "transactionHash": _TX,
        "logIndex": log_index,
        "blockHash": _BLOCK,
        "blockNumber": "0x10",
        "removed": removed,
    }


def test_same_block_other_asset_transfer_is_not_mis_attributed() -> None:
    """Asset A and asset B both hit the payee in one block; each scopes to its own credit."""
    logs = [
        _log(asset=_ASSET_A, payee=_PAYEE, value=1000, log_index="0x1"),
        _log(asset=_ASSET_B, payee=_PAYEE, value=1000, log_index="0x2"),
    ]
    a = reconcile_asset_scoped(
        logs, set(), chain_id=1, expected_asset=_ASSET_A, expected_payee=_PAYEE
    )
    assert len(a) == 1
    assert a[0].asset == _ASSET_A
    b = reconcile_asset_scoped(
        logs, set(), chain_id=1, expected_asset=_ASSET_B, expected_payee=_PAYEE
    )
    assert len(b) == 1
    assert b[0].asset == _ASSET_B


def test_decoy_other_asset_leaves_the_expected_asset_order_unpaid() -> None:
    """Only asset B arrives; an asset-A order finds no A credit and stays unreconciled."""
    logs = [_log(asset=_ASSET_B, payee=_PAYEE, value=1000, log_index="0x1")]
    unreconciled = reconcile_asset_scoped(
        logs, set(), chain_id=1, expected_asset=_ASSET_A, expected_payee=_PAYEE
    )
    assert unreconciled == []


def test_strict_find_unreconciled_still_fails_closed_on_mixed_assets() -> None:
    """The strict path must keep refusing a mixed snapshot rather than pick silently."""
    logs = [
        _log(asset=_ASSET_A, payee=_PAYEE, value=1000, log_index="0x1"),
        _log(asset=_ASSET_B, payee=_PAYEE, value=1000, log_index="0x2"),
    ]
    with pytest.raises(ReconciliationError, match="cross-asset"):
        find_unreconciled(logs, set(), chain_id=1, expected_asset=_ASSET_A)


def test_scoped_reconciliation_respects_the_ledger() -> None:
    """An expected-asset credit already in the ledger is not returned as unreconciled."""
    logs = [_log(asset=_ASSET_A, payee=_PAYEE, value=1000, log_index="0x1")]
    known = decode_transfer_log(logs[0], chain_id=1)
    assert reconcile_asset_scoped(logs, {known.identity}, chain_id=1, expected_asset=_ASSET_A) == []


def test_cross_payee_within_the_scoped_asset_fails_closed() -> None:
    """A scoped-asset transfer to a different payee is a cross-recipient error, not a credit."""
    logs = [_log(asset=_ASSET_A, payee=_OTHER, value=1000, log_index="0x1")]
    with pytest.raises(ReconciliationError, match="cross-recipient"):
        reconcile_asset_scoped(
            logs, set(), chain_id=1, expected_asset=_ASSET_A, expected_payee=_PAYEE
        )


def test_removed_scoped_log_is_not_credited() -> None:
    """A reorged (removed) expected-asset log never counts as a settlement."""
    logs = [_log(asset=_ASSET_A, payee=_PAYEE, value=1000, log_index="0x1", removed=True)]
    assert reconcile_asset_scoped(logs, set(), chain_id=1, expected_asset=_ASSET_A) == []
