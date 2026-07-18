"""Independent ABI calibration and malformed input tests (PSV-AUD-006)."""

from __future__ import annotations

import pytest
from eth_abi import encode
from hypothesis import given
from hypothesis import strategies as st

from psv.chain import SEL_TRANSFER_WITH_AUTHORIZATION, TokenView, _slot_addr, _slot_bytes32

ADDRESS = "0x" + "11" * 20
PAYEE = "0x" + "22" * 20
NONCE = "0x" + "33" * 32
SIGNATURE = "0x" + "44" * 65


def _token() -> TokenView:
    return TokenView(None, "0x" + "55" * 20)  # type: ignore[arg-type]


def test_settle_calldata_matches_eth_abi_exactly() -> None:
    calldata = _token().settle_calldata(
        from_addr=ADDRESS,
        to=PAYEE,
        value=2**255 + 7,
        valid_after=1,
        valid_before=2**256 - 1,
        nonce=NONCE,
        signature=SIGNATURE,
    )
    expected = bytes.fromhex(SEL_TRANSFER_WITH_AUTHORIZATION) + encode(
        ["address", "address", "uint256", "uint256", "uint256", "bytes32", "bytes"],
        [
            ADDRESS,
            PAYEE,
            2**255 + 7,
            1,
            2**256 - 1,
            bytes.fromhex(NONCE[2:]),
            bytes.fromhex(SIGNATURE[2:]),
        ],
    )
    assert bytes.fromhex(calldata[2:]) == expected


@given(size=st.integers(min_value=0, max_value=80).filter(lambda size: size != 20))
def test_address_must_be_exactly_20_bytes(size: int) -> None:
    with pytest.raises(ValueError):
        _slot_addr("0x" + "11" * size)


@given(size=st.integers(min_value=0, max_value=80).filter(lambda size: size != 32))
def test_nonce_must_be_exactly_32_bytes(size: int) -> None:
    with pytest.raises(ValueError):
        _slot_bytes32("0x" + "11" * size)


@pytest.mark.parametrize(
    "signature", ["", "0x", "0x1", "0x" + "11" * 64, "0x" + "11" * 66, "0x" + "gg" * 65]
)
def test_signature_must_be_exactly_65_bytes(signature: str) -> None:
    with pytest.raises(ValueError, match="65 bytes"):
        _token().settle_calldata(
            from_addr=ADDRESS,
            to=PAYEE,
            value=1,
            valid_after=0,
            valid_before=1,
            nonce=NONCE,
            signature=signature,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"value": 0},
        {"value": -1},
        {"value": 2**256},
        {"valid_after": -1},
        {"valid_before": 0},
        {"valid_before": 2**256},
        {"valid_after": 5, "valid_before": 5},
        {"valid_after": 6, "valid_before": 5},
        {"from_addr": "0x" + "00" * 20},
        {"to": "0x" + "00" * 20},
    ],
)
def test_settle_domain_fails_before_encoding(overrides: dict[str, object]) -> None:
    values: dict[str, object] = {
        "from_addr": ADDRESS,
        "to": PAYEE,
        "value": 1,
        "valid_after": 0,
        "valid_before": 1,
        "nonce": NONCE,
        "signature": SIGNATURE,
    }
    values.update(overrides)
    with pytest.raises(ValueError):
        _token().settle_calldata(**values)  # type: ignore[arg-type]


class _PinnedRpc:
    def __init__(self) -> None:
        self.blocks: list[int | str] = []

    def eth_call(self, _to: str, _data: str, block: int | str = "latest") -> str:
        self.blocks.append(block)
        return "0x" + "00" * 32


def test_token_reads_forward_explicit_block_pin() -> None:
    rpc = _PinnedRpc()
    token = TokenView(rpc, "0x" + "55" * 20)  # type: ignore[arg-type]
    assert token.balance_of(ADDRESS, block=123) == 0
    assert token.authorization_used(ADDRESS, NONCE, block="safe") is False
    assert rpc.blocks == [123, "safe"]
