"""Exact arithmetic for token decimals and fee-on-transfer behavior."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

_UINT256_MAX = 2**256 - 1
_MAX_DECIMALS = 255  # ERC-20 ``decimals()`` is conventionally a uint8.


def _decimals(value: int) -> int:
    """Validate a conventional ERC-20 uint8 decimal count."""
    if type(value) is not int or not 0 <= value <= _MAX_DECIMALS:
        raise ValueError(f"decimals must be an integer within [0, {_MAX_DECIMALS}]")
    return value


def _uint256(value: int, what: str) -> int:
    """Validate an exact non-negative integer in the uint256 domain."""
    if type(value) is not int or not 0 <= value <= _UINT256_MAX:
        raise ValueError(f"{what} must be a uint256")
    return value


def to_atomic(human: str | int, decimals: int) -> int:
    """Convert a finite, non-negative human amount to atomic units exactly."""
    places = _decimals(decimals)
    if isinstance(human, bool) or not isinstance(human, (str, int)):
        raise ValueError("human amount must be a decimal string or integer")
    if isinstance(human, str) and len(human) > 1024:
        raise ValueError("human amount exceeds the input size limit")
    try:
        amount = Decimal(str(human))
    except InvalidOperation as exc:
        raise ValueError(f"invalid human amount: {human!r}") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("human amount must be finite and non-negative")
    decimal_tuple = amount.as_tuple()
    exponent = decimal_tuple.exponent
    if not isinstance(exponent, int):  # guarded by is_finite(), for type checkers
        raise ValueError("human amount must be finite")
    coefficient = int("".join(str(digit) for digit in decimal_tuple.digits) or "0")
    scaled_exponent = exponent + places
    if scaled_exponent >= 0:
        if coefficient and len(str(coefficient)) + scaled_exponent > 78:
            raise ValueError("atomic amount must be a uint256")
        atomic = coefficient * (10**scaled_exponent)
    else:
        divisor_places = -scaled_exponent
        if coefficient and divisor_places > len(str(coefficient)):
            raise ValueError(f"{human!r} is not representable in {places} decimals")
        divisor = 10**divisor_places
        atomic, remainder = divmod(coefficient, divisor)
        if remainder:
            raise ValueError(f"{human!r} is not representable in {places} decimals")
    if decimal_tuple.sign:
        atomic = -atomic
    if atomic < 0:
        raise ValueError("human amount must be non-negative")
    return _uint256(atomic, "atomic amount")


def from_atomic(atomic: int, decimals: int) -> str:
    """Convert a uint256 atomic amount to a human decimal string exactly."""
    value = _uint256(atomic, "atomic amount")
    places = _decimals(decimals)
    if places == 0:
        return str(value)
    digits = str(value).rjust(places + 1, "0")
    whole, fraction = digits[:-places], digits[-places:]
    fraction = fraction.rstrip("0")
    return whole if not fraction else f"{whole}.{fraction}"


def net_after_fee(gross: int, fee_bps: int) -> int:
    """Amount the recipient nets after a basis-point fee (rounded down)."""
    amount = _uint256(gross, "gross")
    if type(fee_bps) is not int or not 0 <= fee_bps <= 10_000:
        raise ValueError("fee_bps must be an integer within [0, 10000]")
    return amount - (amount * fee_bps) // 10_000


def received_is_sufficient(received: int, required: int) -> bool:
    """Return whether the merchant's exact net receipt covers the requirement."""
    return _uint256(received, "received") >= _uint256(required, "required")


def underpayment(received: int, required: int) -> int:
    """How much the merchant is short (zero if fully paid)."""
    actual = _uint256(received, "received")
    expected = _uint256(required, "required")
    return max(0, expected - actual)
