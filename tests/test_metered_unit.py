"""Unit tests for the upto (metered) divergence detector — PSV-RD-006.

The upto scheme authorizes a *maximum* and settles the actual usage up to that cap,
consuming the authorization at most once. So the mirror of the exact-scheme check
applies: a settlement *below* the cap is a healthy partial payment, while a settlement
*above* the cap is the money bug (OVER_AUTHORIZED_SETTLEMENT). Replay of a consumed
authorization moves nothing on-chain, so a re-credit surfaces as PHANTOM_CREDIT.
"""

from __future__ import annotations

import pytest

from psv.chain import SettlementTruth
from psv.divergence import DivergenceKind, Severity, detect_metered_divergence


def _truth(nonce: bool, payer_delta: int, payee_delta: int) -> SettlementTruth:
    return SettlementTruth(
        nonce_consumed=nonce,
        payer_balance_after=0,
        payee_balance_after=0,
        payer_delta=payer_delta,
        payee_delta=payee_delta,
    )


def test_partial_settlement_below_cap_is_healthy() -> None:
    # Metered billing: the merchant nets less than the authorized maximum on purpose.
    d = detect_metered_divergence(
        _truth(True, -400, 400), sut_believes_paid=True, authorized_max=1000
    )
    assert d.kind is DivergenceKind.CONSISTENT_PAID
    assert not d.is_failure


def test_settlement_exactly_at_cap_is_healthy() -> None:
    d = detect_metered_divergence(
        _truth(True, -1000, 1000), sut_believes_paid=True, authorized_max=1000
    )
    assert d.kind is DivergenceKind.CONSISTENT_PAID
    assert not d.is_failure


def test_settlement_above_cap_is_over_authorized() -> None:
    d = detect_metered_divergence(
        _truth(True, -1500, 1500), sut_believes_paid=True, authorized_max=1000
    )
    assert d.kind is DivergenceKind.OVER_AUTHORIZED_SETTLEMENT
    assert d.severity is Severity.CRITICAL
    assert d.is_failure
    assert "over by 500" in d.message


def test_replayed_authorization_credited_again_is_phantom_credit() -> None:
    # Second settle of a consumed upto authorization moves nothing on-chain; a re-credit
    # is a PHANTOM_CREDIT — the "settle at most once" violation needs no separate kind.
    d = detect_metered_divergence(_truth(True, 0, 0), sut_believes_paid=True, authorized_max=1000)
    assert d.kind is DivergenceKind.PHANTOM_CREDIT
    assert d.is_failure


def test_funds_moved_but_sut_unpaid_is_silent_loss() -> None:
    d = detect_metered_divergence(
        _truth(True, -500, 500), sut_believes_paid=False, authorized_max=1000
    )
    assert d.kind is DivergenceKind.SILENT_LOSS
    assert d.is_failure


def test_no_funds_and_unpaid_is_consistent() -> None:
    d = detect_metered_divergence(_truth(False, 0, 0), sut_believes_paid=False, authorized_max=1000)
    assert d.kind is DivergenceKind.CONSISTENT_UNPAID
    assert not d.is_failure


def test_zero_cap_rejects_any_positive_settlement() -> None:
    # An authorized maximum of zero authorizes nothing: any funds moved exceed it.
    d = detect_metered_divergence(_truth(True, -1, 1), sut_believes_paid=True, authorized_max=0)
    assert d.kind is DivergenceKind.OVER_AUTHORIZED_SETTLEMENT
    assert d.is_failure


def test_negative_cap_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        detect_metered_divergence(_truth(True, -1, 1), sut_believes_paid=True, authorized_max=-1)
