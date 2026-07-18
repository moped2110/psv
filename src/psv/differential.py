"""Differential testing — run one on-chain fact past several SUT beliefs at once.

The divergence detector grades ONE system's belief against chain truth. Differential
testing drives the *same* on-chain state through multiple System-under-Test
implementations (or one system in several configurations) and flags any whose
belief disagrees with the others: on a single, unambiguous on-chain fact, two
correct systems must agree, so a disagreement localises the faulty one.

This is transport-agnostic and pure — the beliefs are booleans the caller has
already obtained from each SUT (e.g. via the confirmer or an HTTP /status). It
composes with :func:`psv.divergence.detect_payment_divergence` to grade each.
"""

from __future__ import annotations

from dataclasses import dataclass

from .chain import SettlementTruth
from .divergence import Divergence, detect_payment_divergence


@dataclass
class DifferentialResult:
    """The graded outcome of one payment across several SUTs against one chain truth."""

    verdicts: dict[str, Divergence]  # SUT name -> its divergence vs. chain truth
    disagree: bool  # did the SUTs' paid-beliefs differ at all?
    failing: dict[str, Divergence]  # the SUTs whose belief is a CRITICAL divergence

    @property
    def has_finding(self) -> bool:
        """True if any SUT diverged critically from chain truth."""
        return bool(self.failing)


def run_differential(
    chain: SettlementTruth,
    beliefs: dict[str, bool],
    *,
    required_amount: int | None = None,
) -> DifferentialResult:
    """Grade each SUT's ``believes_paid`` against the SAME ``chain`` truth.

    ``beliefs`` maps a SUT name to what that system thinks about the payment. On
    one on-chain fact, differing beliefs mean at least one SUT is wrong — the
    ``failing`` map names those whose belief is a money-losing divergence.
    """
    if not beliefs:
        raise ValueError("beliefs must contain at least one SUT")
    if any(not isinstance(name, str) or not name for name in beliefs):
        raise ValueError("every SUT name must be a non-empty string")
    if any(type(belief) is not bool for belief in beliefs.values()):
        raise ValueError("every SUT belief must be a boolean")
    if required_amount is not None and (
        type(required_amount) is not int or not 0 <= required_amount <= 2**256 - 1
    ):
        raise ValueError("required_amount must be a uint256 or null")
    verdicts = {
        name: detect_payment_divergence(chain, believes, required_amount=required_amount)
        for name, believes in beliefs.items()
    }
    failing = {name: v for name, v in verdicts.items() if v.is_failure}
    disagree = len(set(beliefs.values())) > 1
    return DifferentialResult(verdicts=verdicts, disagree=disagree, failing=failing)
