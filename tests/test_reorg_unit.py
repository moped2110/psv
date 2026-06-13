"""Offline tests for the R-class reorg/finality logic + RPC resilience."""

from __future__ import annotations

from typing import Any

import pytest

from psv.divergence import DivergenceKind, detect_payment_divergence, settlement_truth_from_balances
from psv.reference_sut.confirmer import EventWatchingConfirmer
from psv.reorg import confirmations, is_final, reorg_to, take_checkpoint


def test_confirmations_math() -> None:
    assert confirmations(current_block=100, tx_block=100) == 1  # just mined
    assert confirmations(current_block=105, tx_block=100) == 6
    assert confirmations(current_block=99, tx_block=100) == 0   # not yet mined
    assert confirmations(current_block=100, tx_block=0) == 0    # no tx block


def test_is_final_threshold() -> None:
    assert is_final(current_block=100, tx_block=100, required_confirmations=5) is False  # shallow
    assert is_final(current_block=104, tx_block=100, required_confirmations=5) is True   # deep enough


class _SnapRpc:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def snapshot(self) -> str:
        self.calls.append(("snapshot",))
        return "0x1"

    def revert(self, snapshot_id: str) -> bool:
        self.calls.append(("revert", snapshot_id))
        return True


def test_checkpoint_and_reorg_drive_snapshot_revert() -> None:
    rpc = _SnapRpc()
    cp = take_checkpoint(rpc)  # type: ignore[arg-type]
    assert cp == "0x1"
    assert reorg_to(rpc, cp) is True  # type: ignore[arg-type]
    assert rpc.calls == [("snapshot",), ("revert", "0x1")]


def test_reorg_scenario_is_phantom_credit() -> None:
    # Before the reorg the payment settled; after the reorg the funds are gone
    # (balances back to baseline, nonce free) but the system still believes paid.
    truth_after_reorg = settlement_truth_from_balances(
        nonce_consumed=False, payer_before=1_000_000, payer_after=1_000_000,
        payee_before=0, payee_after=0,
    )
    d = detect_payment_divergence(truth_after_reorg, sut_believes_paid=True)
    assert d.kind is DivergenceKind.PHANTOM_CREDIT
    assert d.is_failure


def test_confirmer_does_not_swallow_rpc_error_into_false_negative() -> None:
    # A robust system must NOT turn an RPC failure into a silent "unpaid".
    def broken_fetch(addr: str, topics: list[Any], from_block: int) -> list[dict[str, Any]]:
        raise ConnectionError("RPC node unreachable")

    c = EventWatchingConfirmer(fetch_logs=broken_fetch)
    with pytest.raises(ConnectionError):
        c.is_settled(token="0xt", payer="0xp", payee="0xm", min_value=1)
