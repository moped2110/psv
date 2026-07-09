"""Property-based tests for the divergence detector (Hypothesis).

The detector is the harness's core logic, so instead of only spot-checking the
four quadrants we assert its invariants hold across the whole input space: it is
a total function, its grading matches an independently-derived truth table, and
UNDERPAID_CREDIT appears only when a required amount is supplied and undershot.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from psv.chain import SettlementTruth
from psv.divergence import DivergenceKind, Severity, detect_payment_divergence

_FAILURE_KINDS = {
    DivergenceKind.SILENT_LOSS,
    DivergenceKind.PHANTOM_CREDIT,
    DivergenceKind.UNDERPAID_CREDIT,
}

# Token base units span a wide range; go past uint64 to catch any width assumption.
_amounts = st.integers(min_value=-(2**80), max_value=2**80)
_required = st.one_of(st.none(), st.integers(min_value=0, max_value=2**80))


def _truth(nonce: bool, payer_delta: int, payee_delta: int) -> SettlementTruth:
    # balances_after are irrelevant to detection; only deltas + nonce feed funds_moved.
    return SettlementTruth(
        nonce_consumed=nonce,
        payer_balance_after=0,
        payee_balance_after=0,
        payer_delta=payer_delta,
        payee_delta=payee_delta,
    )


@given(
    nonce=st.booleans(),
    payer_delta=_amounts,
    payee_delta=_amounts,
    believes=st.booleans(),
    required=_required,
)
def test_grading_matches_independent_truth_table(
    nonce: bool, payer_delta: int, payee_delta: int, believes: bool, required: int | None
) -> None:
    truth = _truth(nonce, payer_delta, payee_delta)
    d = detect_payment_divergence(truth, sut_believes_paid=believes, required_amount=required)

    # funds_moved re-derived here, independently of the detector.
    moved = nonce and payer_delta < 0 and payee_delta > 0
    if moved and believes:
        if required is not None and payee_delta < required:
            expected = DivergenceKind.UNDERPAID_CREDIT
        else:
            expected = DivergenceKind.CONSISTENT_PAID
    elif not moved and not believes:
        expected = DivergenceKind.CONSISTENT_UNPAID
    elif moved and not believes:
        expected = DivergenceKind.SILENT_LOSS
    else:
        expected = DivergenceKind.PHANTOM_CREDIT

    assert d.kind is expected
    # Severity and is_failure are a pure function of the kind.
    assert d.is_failure == (d.kind in _FAILURE_KINDS)
    assert (d.severity is Severity.CRITICAL) == (d.kind in _FAILURE_KINDS)
    assert d.message  # always human-readable, never empty


@given(
    nonce=st.booleans(),
    payer_delta=_amounts,
    payee_delta=_amounts,
    believes=st.booleans(),
)
def test_underpaid_needs_a_required_amount(
    nonce: bool, payer_delta: int, payee_delta: int, believes: bool
) -> None:
    # Without a required amount the detector cannot see a shortfall, so it must
    # never emit UNDERPAID_CREDIT (backward-compatible with pre-required callers).
    d = detect_payment_divergence(_truth(nonce, payer_delta, payee_delta), believes)
    assert d.kind is not DivergenceKind.UNDERPAID_CREDIT


@given(
    payee=st.integers(min_value=1, max_value=2**80),
    required=st.integers(min_value=0, max_value=2**80),
)
def test_paid_iff_enough_when_credited(payee: int, required: int) -> None:
    # Money moved and the SUT credited: paid exactly when the merchant netted >= price.
    truth = _truth(nonce=True, payer_delta=-payee, payee_delta=payee)
    d = detect_payment_divergence(truth, sut_believes_paid=True, required_amount=required)
    if payee >= required:
        assert d.kind is DivergenceKind.CONSISTENT_PAID
        assert not d.is_failure
    else:
        assert d.kind is DivergenceKind.UNDERPAID_CREDIT
        assert d.is_failure
