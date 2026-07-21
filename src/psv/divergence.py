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
    """The ways chain truth and SUT belief can line up. The two consistent cases
    are healthy; the asymmetric ones are the bugs worth money. UNDERPAID_CREDIT is
    the subtle case funds_moved alone misses: money moved, but not *enough*."""

    CONSISTENT_PAID = "consistent_paid"
    CONSISTENT_UNPAID = "consistent_unpaid"
    SILENT_LOSS = "silent_loss"  # funds moved on-chain, SUT believes unpaid
    PHANTOM_CREDIT = "phantom_credit"  # SUT believes paid, no funds moved
    UNDERPAID_CREDIT = "underpaid_credit"  # SUT believes paid, but < required arrived (exact)
    # SUT believes paid, but > authorized maximum left the payer (upto/metered scheme)
    OVER_AUTHORIZED_SETTLEMENT = "over_authorized_settlement"


class Severity(str, Enum):
    """Whether a divergence is benign (OK) or a money/security bug (CRITICAL)."""

    OK = "ok"
    CRITICAL = "critical"


@dataclass
class Divergence:
    """The graded verdict for one payment: which `kind` of (mis)match, how bad,
    and a human-readable explanation. `is_failure` is the gate for tests."""

    kind: DivergenceKind
    severity: Severity
    message: str

    @property
    def is_failure(self) -> bool:
        """Return whether this verdict represents a critical system mismatch."""
        return self.severity is Severity.CRITICAL


def detect_payment_divergence(
    chain: SettlementTruth,
    sut_believes_paid: bool,
    required_amount: int | None = None,
) -> Divergence:
    """Compare on-chain ground truth against the SUT's belief about one payment.

    ``required_amount`` (the invoiced amount, in token base units) is optional and
    backward-compatible: when given, a credited order where the merchant received
    *less* than the invoice is flagged as UNDERPAID_CREDIT. ``funds_moved`` only
    asks "did any money move?"; it cannot see a partial payment or a token that
    skims a fee in transit, so without ``required_amount`` those slip through as
    healthy. We compare against ``payee_delta`` — what the merchant actually netted
    — not what the payer was charged, since the merchant's exposure is the shortfall.
    """
    moved = chain.funds_moved
    if moved and sut_believes_paid:
        if required_amount is not None and chain.payee_delta < required_amount:
            short = required_amount - chain.payee_delta
            return Divergence(
                DivergenceKind.UNDERPAID_CREDIT,
                Severity.CRITICAL,
                (
                    "UNDERPAID CREDIT: the SUT credited the order as paid, but the merchant "
                    f"received {chain.payee_delta:d} on-chain against a required "
                    f"{required_amount:d} — short by {short:d}. The order is handed out for "
                    "less than its price: a partial payment, or a fee-on-transfer / rebasing "
                    "token skimming the difference, waved through as full settlement."
                ),
            )
        enough = (
            f" Received {chain.payee_delta:d} >= required {required_amount:d}."
            if required_amount is not None
            else ""
        )
        return Divergence(
            DivergenceKind.CONSISTENT_PAID,
            Severity.OK,
            "Funds moved on-chain and the SUT correctly registered the payment." + enough,
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


def detect_metered_divergence(
    chain: SettlementTruth,
    sut_believes_paid: bool,
    authorized_max: int,
) -> Divergence:
    """Compare on-chain truth against the SUT's belief for an ``upto`` (metered) payment.

    The ``upto`` scheme authorizes a *maximum* (``authorized_max``); the resource server
    settles the actual usage, which MUST be ``<=`` that cap, and the authorization is
    consumed at most once (x402 ``specs/schemes/upto``). This is the mirror image of
    :func:`detect_payment_divergence`'s exact-scheme ``required_amount`` check: there,
    receiving *less* than required is the bug; here, moving *more* than authorized is —
    receiving less than the cap is a healthy partial settlement, the whole point of
    metered billing.

    The bug unique to ``upto`` is ``OVER_AUTHORIZED_SETTLEMENT``: more than the cap left
    the payer. Replay of a consumed authorization (the "settle at most once" violation)
    needs no separate kind — the second settlement moves nothing on-chain, so a SUT that
    credits it again surfaces as ``PHANTOM_CREDIT``, exactly as this detector returns for
    the no-funds-moved case.
    """
    if type(authorized_max) is not int or authorized_max < 0:
        raise ValueError("authorized_max must be a non-negative integer (token base units)")
    moved = chain.funds_moved
    if moved and sut_believes_paid:
        if chain.payee_delta > authorized_max:
            over = chain.payee_delta - authorized_max
            return Divergence(
                DivergenceKind.OVER_AUTHORIZED_SETTLEMENT,
                Severity.CRITICAL,
                (
                    "OVER-AUTHORIZED SETTLEMENT: the SUT credited an upto (metered) order, but "
                    f"the merchant received {chain.payee_delta:d} on-chain against an authorized "
                    f"maximum of {authorized_max:d} — over by {over:d}. The facilitator settled "
                    "beyond the client's cap; an upto authorization MUST settle at most the "
                    "authorized maximum."
                ),
            )
        return Divergence(
            DivergenceKind.CONSISTENT_PAID,
            Severity.OK,
            (
                f"Funds moved on-chain ({chain.payee_delta:d}) within the authorized maximum "
                f"{authorized_max:d}, and the SUT correctly registered the metered payment."
            ),
        )
    if not moved and not sut_believes_paid:
        return Divergence(
            DivergenceKind.CONSISTENT_UNPAID,
            Severity.OK,
            "No funds moved and the SUT correctly treats the metered order as unpaid.",
        )
    if moved and not sut_believes_paid:
        return Divergence(
            DivergenceKind.SILENT_LOSS,
            Severity.CRITICAL,
            (
                "SILENT LOSS: the payer was debited and the merchant credited on-chain "
                f"(payer {chain.payer_delta:+d}, payee {chain.payee_delta:+d}, nonce "
                f"consumed={chain.nonce_consumed}), but the SUT believes the metered order is "
                "unpaid. The customer paid and gets nothing."
            ),
        )
    return Divergence(
        DivergenceKind.PHANTOM_CREDIT,
        Severity.CRITICAL,
        (
            "PHANTOM CREDIT: the SUT believes the metered order is paid, but no funds moved "
            f"on-chain (nonce consumed={chain.nonce_consumed}, payee delta "
            f"{chain.payee_delta:+d}). Either nothing settled, or a consumed upto "
            "authorization was replayed and credited again. The resource is handed out free."
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
