"""Quote-as-free-option (G3): a priced quote is a call option the buyer never pays for.

When a payment system issues a quote that locks a price for a resource whose fair
value moves, and honors that quote later without re-checking the price, the buyer
holds a **free option**: request many quotes, execute only the ones that became
favorable (fair value rose above the locked price), abandon the rest at no cost.
Because the buyer only ever exercises in-the-money, the system's expected outcome
is a systematic loss — no "attack", just market logic the design ignored.

This module is pure economics, fully offline-testable:

  * ``option_value`` — the buyer's gain (= system's loss) if a quote is exercised.
  * ``quote_is_stale`` — whether a locked price is exploitably below current fair
    value (beyond a tolerance). A re-pricing guard rejects exactly these.
  * ``simulate_attacker`` — a rational buyer over many rounds; quantifies the loss
    a vulnerable system bleeds versus a guarded one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


def option_value(locked_price: int, fair_value_now: int) -> int:
    """Buyer's gain from exercising a quote = max(0, fair_value − locked).

    Zero when the quote is at/above fair value (out of the money): the rational
    buyer simply walks away, costing nothing.
    """
    return max(0, fair_value_now - locked_price)


def quote_is_stale(locked_price: int, fair_value_now: int, tolerance: float = 0.0) -> bool:
    """True when honoring the quote underprices the resource beyond ``tolerance``.

    ``tolerance`` is the fraction of slack a system allows before it must re-price
    (e.g. 0.02 = 2%). A robust system rejects/re-quotes stale quotes; a vulnerable
    one honors them and eats the difference.
    """
    if locked_price <= 0:
        return fair_value_now > 0
    return fair_value_now > locked_price * (1.0 + tolerance)


@dataclass
class Round:
    """One quote in a simulation: price locked at quote time, fair value at exercise."""

    locked_price: int
    fair_value_at_exercise: int


@dataclass
class ExploitResult:
    rounds: int
    exercised: int
    system_loss: int  # total value handed to the rational buyer

    @property
    def is_exploitable(self) -> bool:
        return self.system_loss > 0


def simulate_attacker(
    rounds: Sequence[Round], *, tolerance: float = 0.0, reprice: bool
) -> ExploitResult:
    """Run a rational buyer over ``rounds`` against a system with/without a guard.

    The buyer exercises iff the quote is in the money. A re-pricing system
    (``reprice=True``) refuses stale quotes within ``tolerance``, so the buyer can
    never exercise a profitable one — driving the loss to zero. A vulnerable
    system (``reprice=False``) honors every quote and bleeds the option value.
    """
    exercised = 0
    loss = 0
    for r in rounds:
        gain = option_value(r.locked_price, r.fair_value_at_exercise)
        if gain <= 0:
            continue  # out of the money — buyer abandons, no cost to either side
        if reprice and quote_is_stale(r.locked_price, r.fair_value_at_exercise, tolerance):
            continue  # guarded system rejects the stale quote before settling
        exercised += 1
        loss += gain
    return ExploitResult(rounds=len(rounds), exercised=exercised, system_loss=loss)
