"""Offline unit tests for the quote-as-option economics (G3). No chain.

We prove the core asymmetry (a buyer only exercises in-the-money) and that a
re-pricing guard drives the system's loss to zero.
"""

from __future__ import annotations

from psv.quote_option import Round, option_value, quote_is_stale, simulate_attacker


def test_option_value_only_when_in_the_money() -> None:
    assert option_value(10_000, 15_000) == 5_000  # fair rose -> buyer gains
    assert option_value(10_000, 10_000) == 0
    assert option_value(10_000, 6_000) == 0  # out of the money -> buyer walks


def test_quote_is_stale_respects_tolerance() -> None:
    assert quote_is_stale(10_000, 10_300, tolerance=0.02) is True  # +3% > 2%
    assert quote_is_stale(10_000, 10_100, tolerance=0.02) is False  # +1% within slack
    assert quote_is_stale(10_000, 9_000) is False  # below fair, never stale


def _rounds() -> list[Round]:
    # A random-ish walk: some up (exploitable), some down (abandoned).
    return [
        Round(10_000, 13_000),  # +3000 in the money
        Round(10_000, 8_000),   # out -> abandoned
        Round(10_000, 11_000),  # +1000
        Round(10_000, 9_500),   # out -> abandoned
        Round(10_000, 20_000),  # +10000
    ]


def test_vulnerable_system_bleeds_the_option_value() -> None:
    res = simulate_attacker(_rounds(), reprice=False)
    assert res.exercised == 3
    assert res.system_loss == 14_000
    assert res.is_exploitable


def test_repricing_guard_eliminates_the_loss() -> None:
    res = simulate_attacker(_rounds(), reprice=True, tolerance=0.02)
    # Every in-the-money quote is stale beyond 2% -> all rejected before settling.
    assert res.exercised == 0
    assert res.system_loss == 0
    assert not res.is_exploitable
