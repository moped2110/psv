"""Exact quote-as-free-option economics (G3)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_UINT256_MAX = 2**256 - 1


def _amount(value: int, what: str) -> int:
    """Validate an exact atomic amount within the uint256 domain."""
    if type(value) is not int or not 0 <= value <= _UINT256_MAX:
        raise ValueError(f"{what} must be a uint256")
    return value


def _tolerance(value: Decimal | float | int | str) -> Decimal:
    """Normalize a finite fractional tolerance without binary float arithmetic."""
    if isinstance(value, bool) or not isinstance(value, (Decimal, float, int, str)):
        raise ValueError("tolerance must be a decimal value")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid tolerance") from exc
    if not parsed.is_finite() or not 0 <= parsed <= 1:
        raise ValueError("tolerance must be finite and within [0, 1]")
    return parsed


def option_value(locked_price: int, fair_value_now: int) -> int:
    """Buyer's gain from exercising a quote, using exact atomic units."""
    locked = _amount(locked_price, "locked_price")
    fair = _amount(fair_value_now, "fair_value_now")
    return max(0, fair - locked)


def quote_is_stale(
    locked_price: int,
    fair_value_now: int,
    tolerance: Decimal | float | int | str = Decimal(0),
) -> bool:
    """Return whether fair value exceeds the exact tolerated quote price."""
    locked = _amount(locked_price, "locked_price")
    fair = _amount(fair_value_now, "fair_value_now")
    slack = _tolerance(tolerance)
    if locked == 0:
        return fair > 0
    numerator, denominator = slack.as_integer_ratio()
    return fair * denominator > locked * (denominator + numerator)


@dataclass(frozen=True)
class Round:
    """One locked quote and its fair value at exercise."""

    locked_price: int
    fair_value_at_exercise: int


@dataclass(frozen=True)
class ExploitResult:
    """Aggregate quote-option exercise count and exact system loss."""

    rounds: int
    exercised: int
    system_loss: int

    @property
    def is_exploitable(self) -> bool:
        """Return whether rational quote exercise creates positive system loss."""
        return self.system_loss > 0


def simulate_attacker(
    rounds: Sequence[Round],
    *,
    tolerance: Decimal | float | int | str = Decimal(0),
    reprice: bool,
) -> ExploitResult:
    """Run a rational buyer across validated, exact-price rounds."""
    if type(reprice) is not bool:
        raise ValueError("reprice must be a boolean")
    slack = _tolerance(tolerance)
    exercised = 0
    loss = 0
    for round_ in rounds:
        if not isinstance(round_, Round):
            raise ValueError("rounds must contain Round values")
        gain = option_value(round_.locked_price, round_.fair_value_at_exercise)
        if gain <= 0:
            continue
        if reprice and quote_is_stale(round_.locked_price, round_.fair_value_at_exercise, slack):
            continue
        exercised += 1
        loss += gain
        if loss > _UINT256_MAX:
            raise ValueError("aggregate system_loss exceeds uint256")
    return ExploitResult(rounds=len(rounds), exercised=exercised, system_loss=loss)
