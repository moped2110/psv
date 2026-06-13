"""Token quirks — decimals & fee-on-transfer (Phase 3, T-class).

System-level payment bugs that come from assuming "USDC semantics":

  * **decimals** — computing an amount with the wrong number of decimals over- or
    under-charges by orders of magnitude (a 6-decimals assumption against an
    18-decimals token is off by 10^12).
  * **fee-on-transfer** — the merchant *receives* less than the authorized amount
    because the token skims a fee on transfer. A system that confirms settlement
    on the authorization/event amount (rather than the merchant's actual balance
    delta) is silently underpaid.

Pure arithmetic, fully offline-testable. The chain-truth oracle supplies the real
received delta on-chain; these helpers decide whether it is sufficient.
"""

from __future__ import annotations

from decimal import Decimal


def to_atomic(human: str | int, decimals: int) -> int:
    """Human amount -> atomic units for a token with ``decimals`` places.

    Raises if the amount isn't representable at that precision (e.g. fractional
    atomic units), so a wrong-decimals assumption fails loudly instead of silently
    truncating.
    """
    if decimals < 0:
        raise ValueError("decimals must be >= 0")
    scaled = Decimal(str(human)) * (Decimal(10) ** decimals)
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{human!r} is not representable in {decimals} decimals")
    return int(scaled)


def from_atomic(atomic: int, decimals: int) -> str:
    """Atomic units -> human decimal string."""
    if decimals < 0:
        raise ValueError("decimals must be >= 0")
    return format(Decimal(atomic) / (Decimal(10) ** decimals), "f")


def net_after_fee(gross: int, fee_bps: int) -> int:
    """Amount the recipient nets after a basis-point transfer fee (rounded down)."""
    if not 0 <= fee_bps <= 10_000:
        raise ValueError("fee_bps must be within [0, 10000]")
    return gross - (gross * fee_bps) // 10_000


def received_is_sufficient(received: int, required: int) -> bool:
    """The merchant must NET at least the required amount. Verify on the balance
    delta, never on the authorization or the (possibly gross) Transfer event."""
    return received >= required


def underpayment(received: int, required: int) -> int:
    """How much the merchant is short (0 if fully paid)."""
    return max(0, required - received)
