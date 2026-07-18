"""Property-based tests for the hand-rolled ABI slot encoders (Hypothesis).

Settlement calldata is built by fixed-width slots. A value that doesn't fit in a
32-byte word would silently shift every following slot and corrupt the call, so
the encoders must round-trip the full uint256 range and reject anything wider.
This is the large-value / overflow guard, provable offline.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from psv.chain import TokenView, _slot_addr, _slot_bytes32, _slot_uint

_UINT256_MAX = 2**256 - 1

_uint256 = st.integers(min_value=0, max_value=_UINT256_MAX)
_positive_uint256 = st.integers(min_value=1, max_value=_UINT256_MAX)
_addr = st.integers(min_value=0, max_value=2**160 - 1).map(lambda n: "0x" + f"{n:040x}")


@given(value=_uint256)
def test_slot_uint_round_trips_full_range(value: int) -> None:
    slot = _slot_uint(value)
    assert len(slot) == 64  # exactly one 32-byte word
    assert int(slot, 16) == value


@given(delta=st.integers(min_value=1, max_value=2**64))
def test_slot_uint_rejects_out_of_range(delta: int) -> None:
    with pytest.raises(ValueError):
        _slot_uint(_UINT256_MAX + delta)  # too wide for 256 bits
    with pytest.raises(ValueError):
        _slot_uint(-delta)  # negative


@given(addr=_addr)
def test_slot_addr_round_trips(addr: str) -> None:
    slot = _slot_addr(addr)
    assert len(slot) == 64
    assert slot[-40:] == addr.lower().removeprefix("0x")
    assert slot[:24] == "0" * 24  # left-padded, no bits above 20 bytes


def test_slot_addr_rejects_oversized() -> None:
    with pytest.raises(ValueError):
        _slot_addr("0x" + "ab" * 21)  # 21 bytes > address width
    with pytest.raises(ValueError):
        _slot_bytes32("0x" + "cd" * 33)  # 33 bytes > bytes32


@given(value=_positive_uint256)
def test_settle_calldata_encodes_value_word_faithfully(value: int) -> None:
    # The `value` argument must land in its slot decodable back to the original,
    # even at uint256 max — the amount is the money-carrying field.
    token = TokenView(rpc=None, address="0x" + "22" * 20)  # type: ignore[arg-type]
    data = token.settle_calldata(
        from_addr="0x" + "11" * 20,
        to="0x" + "33" * 20,
        value=value,
        valid_after=0,
        valid_before=_UINT256_MAX,
        nonce="0x" + "44" * 32,
        signature="0x" + "ab" * 65,
    )
    body = data.removeprefix("0x")
    # layout: selector(8) + from(64) + to(64) + value(64) + ...
    value_word = body[8 + 64 + 64 : 8 + 64 + 64 + 64]
    assert int(value_word, 16) == value
