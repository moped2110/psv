"""The divergence detector — the harness's core value.

Black-box conformance asks "does the endpoint speak the protocol?". This asks the
harder question: "does the system's belief match what the chain actually did?".
We hold two independent records of one payment:

  * **chain truth** — read straight from the token (nonce burned, balances moved),
    via :class:`psv.chain.SettlementTruth`; immune to event-signature drift, and
  * **SUT belief** — whether the System-under-Test thinks it got paid.

When they disagree, that gap is the bug. The two asymmetric failures matter very
differently and are graded accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .chain import SettlementTruth


class DivergenceKind(str, Enum):
    CONSISTENT_PAID = "consistent_paid"
    CONSISTENT_UNPAID = "consistent_unpaid"
    SILENT_LOSS = "silent_loss"  # funds moved on-chain, SUT believes unpaid
    PHANTOM_CREDIT = "phantom_credit"  # SUT believes paid, no funds moved


class Severity(str, Enum):
    OK = "ok"
    CRITICAL = "critical"


@dataclass
class Divergence:
    kind: DivergenceKind
    severity: Severity
    message: str

    @property
    def is_failure(self) -> bool:
        return self.severity is Severity.CRITICAL


def detect_payment_divergence(chain: SettlementTruth, sut_believes_paid: bool) -> Divergence:
    """Compare on-chain ground truth against the SUT's belief about one payment."""
    moved = chain.funds_moved
    if moved and sut_believes_paid:
        return Divergence(
            DivergenceKind.CONSISTENT_PAID,
            Severity.OK,
            "Funds moved on-chain and the SUT correctly registered the payment.",
        )
    if not moved and not sut_believes_paid:
        return Divergence(
            DivergenceKind.CONSISTENT_UNPAID,
            Severity.OK,
            "No funds moved and the SUT correctly treats the order as unpaid.",
        )
    if moved and not sut_believes_paid:
        return Divergence(
            DivergenceKind.SILENT_LOSS,
            Severity.CRITICAL,
            (
                "SILENT LOSS: the payer was debited and the merchant credited on-chain "
                f"(payer {chain.payer_delta:+d}, payee {chain.payee_delta:+d}, nonce "
                f"consumed={chain.nonce_consumed}), but the SUT believes the order is "
                "unpaid. The customer paid and gets nothing — the classic SC1 symptom of "
                "a settlement event the system stopped recognizing."
            ),
        )
    return Divergence(
        DivergenceKind.PHANTOM_CREDIT,
        Severity.CRITICAL,
        (
            "PHANTOM CREDIT: the SUT believes the order is paid, but no funds moved "
            f"on-chain (nonce consumed={chain.nonce_consumed}, payee delta "
            f"{chain.payee_delta:+d}). The resource is handed out for free."
        ),
    )


def settlement_truth_from_balances(
    *,
    nonce_consumed: bool,
    payer_before: int,
    payer_after: int,
    payee_before: int,
    payee_after: int,
) -> SettlementTruth:
    """Assemble :class:`SettlementTruth` from before/after balance snapshots."""
    return SettlementTruth(
        nonce_consumed=nonce_consumed,
        payer_balance_after=payer_after,
        payee_balance_after=payee_after,
        payer_delta=payer_after - payer_before,
        payee_delta=payee_after - payee_before,
    )
