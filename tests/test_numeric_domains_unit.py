"""Hostile numeric-domain and exact-arithmetic tests (PSV-AUD-015)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from psv.chain import SettlementTruth
from psv.differential import run_differential
from psv.quote_option import Round, option_value, quote_is_stale, simulate_attacker
from psv.reorg import confirmations, is_final
from psv.token_quirks import (
    from_atomic,
    net_after_fee,
    received_is_sufficient,
    to_atomic,
    underpayment,
)

UINT256_MAX = 2**256 - 1


@pytest.mark.parametrize("amount", ["-1", "NaN", "Infinity", "-Infinity", "1e1000000"])
def test_to_atomic_rejects_negative_nonfinite_or_overflow(amount: str) -> None:
    with pytest.raises(ValueError):
        to_atomic(amount, 6)


@pytest.mark.parametrize("decimals", [-1, 256, True, 1.5])
def test_token_decimals_domain_is_bounded(decimals: object) -> None:
    with pytest.raises(ValueError):
        to_atomic("1", decimals)  # type: ignore[arg-type]


@given(atomic=st.integers(min_value=0, max_value=UINT256_MAX), decimals=st.integers(0, 18))
def test_atomic_human_roundtrip_is_exact(atomic: int, decimals: int) -> None:
    assert to_atomic(from_atomic(atomic, decimals), decimals) == atomic


@pytest.mark.parametrize(
    "call",
    [
        lambda: from_atomic(-1, 6),
        lambda: net_after_fee(-1, 1),
        lambda: net_after_fee(1, -1),
        lambda: received_is_sufficient(-1, 0),
        lambda: received_is_sufficient(0, -1),
        lambda: underpayment(-1, 0),
        lambda: underpayment(0, -1),
    ],
)
def test_token_economic_helpers_reject_negative_domains(call: object) -> None:
    with pytest.raises(ValueError):
        call()  # type: ignore[operator]


@pytest.mark.parametrize("value", [-1, UINT256_MAX + 1, True])
def test_option_amounts_reject_invalid_uint256(value: int) -> None:
    with pytest.raises(ValueError):
        option_value(value, 1)
    with pytest.raises(ValueError):
        option_value(1, value)


@pytest.mark.parametrize("tolerance", [-1, 1.1, float("nan"), float("inf"), "bad", True])
def test_quote_tolerance_rejects_invalid_values(tolerance: object) -> None:
    with pytest.raises(ValueError):
        quote_is_stale(100, 101, tolerance=tolerance)  # type: ignore[arg-type]


def test_quote_comparison_is_decimal_exact_at_large_boundary() -> None:
    locked = 10**70 + 1
    threshold_floor = (locked * 11) // 10
    assert (locked * 11) % 10 == 1
    assert quote_is_stale(locked, threshold_floor, tolerance=Decimal("0.1")) is False
    assert quote_is_stale(locked, threshold_floor + 1, tolerance=Decimal("0.1")) is True


def test_simulation_rejects_uint256_loss_overflow() -> None:
    rounds = [Round(0, UINT256_MAX), Round(0, 1)]
    with pytest.raises(ValueError, match="exceeds uint256"):
        simulate_attacker(rounds, reprice=False)


@pytest.mark.parametrize("args", [(-1, 0), (0, -1), (True, 0), (0, True)])
def test_confirmations_reject_invalid_block_values(args: tuple[object, object]) -> None:
    with pytest.raises(ValueError):
        confirmations(*args)  # type: ignore[arg-type]


@pytest.mark.parametrize("required", [0, -1, True])
def test_finality_requires_positive_confirmation_policy(required: int) -> None:
    with pytest.raises(ValueError):
        is_final(10, 5, required)


def _truth() -> SettlementTruth:
    return SettlementTruth(False, 1, 0, 0, 0)


def test_differential_rejects_empty_or_invalid_beliefs_and_amount() -> None:
    with pytest.raises(ValueError, match="at least one"):
        run_differential(_truth(), {})
    with pytest.raises(ValueError, match="boolean"):
        run_differential(_truth(), {"sut": 1})  # type: ignore[dict-item]
    with pytest.raises(ValueError, match="uint256"):
        run_differential(_truth(), {"sut": False}, required_amount=-1)
