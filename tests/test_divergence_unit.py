"""Offline unit tests for the divergence detector — the harness's core logic.

No chain. We feed the detector hand-built ground truth + SUT beliefs and assert
it grades the four quadrants correctly, with the two asymmetric failures marked
critical.
"""

from __future__ import annotations

from psv.divergence import (
    DivergenceKind,
    Severity,
    detect_payment_divergence,
    settlement_truth_from_balances,
)


def _truth(*, nonce: bool, payer_delta: int, payee_delta: int):
    return settlement_truth_from_balances(
        nonce_consumed=nonce,
        payer_before=1_000_000,
        payer_after=1_000_000 + payer_delta,
        payee_before=0,
        payee_after=0 + payee_delta,
    )


def test_consistent_paid() -> None:
    truth = _truth(nonce=True, payer_delta=-10_000, payee_delta=10_000)
    d = detect_payment_divergence(truth, sut_believes_paid=True)
    assert d.kind is DivergenceKind.CONSISTENT_PAID
    assert d.severity is Severity.OK
    assert not d.is_failure


def test_consistent_unpaid() -> None:
    truth = _truth(nonce=False, payer_delta=0, payee_delta=0)
    d = detect_payment_divergence(truth, sut_believes_paid=False)
    assert d.kind is DivergenceKind.CONSISTENT_UNPAID
    assert not d.is_failure


def test_silent_loss_is_critical() -> None:
    # Funds moved on-chain, but the SUT believes it is unpaid — the SC1 symptom.
    truth = _truth(nonce=True, payer_delta=-10_000, payee_delta=10_000)
    d = detect_payment_divergence(truth, sut_believes_paid=False)
    assert d.kind is DivergenceKind.SILENT_LOSS
    assert d.severity is Severity.CRITICAL
    assert d.is_failure
    assert "SILENT LOSS" in d.message


def test_phantom_credit_is_critical() -> None:
    # SUT believes paid, but nothing moved — free resource.
    truth = _truth(nonce=False, payer_delta=0, payee_delta=0)
    d = detect_payment_divergence(truth, sut_believes_paid=True)
    assert d.kind is DivergenceKind.PHANTOM_CREDIT
    assert d.is_failure


def test_funds_moved_requires_all_signals() -> None:
    # Nonce burned but no balance change (e.g. self-transfer) must not count as moved.
    truth = _truth(nonce=True, payer_delta=0, payee_delta=0)
    assert truth.funds_moved is False
