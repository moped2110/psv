"""Independent chain-truth reads and exact ABI helpers."""

from __future__ import annotations
from typing import final

import re
from dataclasses import dataclass

from .anvil import RpcClient, RpcError

SEL_BALANCE_OF = "70a08231"
SEL_AUTHORIZATION_STATE = "e94a0102"
SEL_TRANSFER_WITH_AUTHORIZATION = "cf092995"
SEL_SET_EVENT_MODE = "2a030f44"
SEL_SET_FEE_BPS = "023b1fc9"
SEL_MINT = "40c10f19"
SEL_EVENT_MODE = "0ce978e2"

TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TOPIC_TRANSFER_V2 = "0x58f9acac7a1c69c84dff9a713e28686566926c704ffaaa5562e8225bdf50911b"
TOPIC_AUTHORIZATION_USED = "0x98de503528ee59b575ef0c0a2576a82497bfc029a5685b209e9ec333479b10a5"

_UINT256_MAX = 2**256 - 1
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_BYTES32_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_SIGNATURE_RE = re.compile(r"^0x[0-9a-fA-F]{130}$")


def _word_to_int(result: object, what: str) -> int:
    """Decode one exact ABI word returned by ``eth_call``."""
    if not isinstance(result, str) or re.fullmatch(r"0x[0-9a-fA-F]{64}", result) is None:
        raise RpcError(f"{what}: malformed 32-byte ABI result {result!r}")
    return int(result, 16)


def _slot_addr(addr: str) -> str:
    """Left-pad one exact 20-byte EVM address to an ABI word."""
    if not isinstance(addr, str) or _ADDRESS_RE.fullmatch(addr) is None:
        raise ValueError(f"not a 20-byte address: {addr!r}")
    return addr[2:].lower().rjust(64, "0")


def _slot_uint(value: int) -> str:
    """Encode one exact uint256 ABI word."""
    if type(value) is not int or not 0 <= value <= _UINT256_MAX:
        raise ValueError(f"uint256 out of range: {value}")
    return f"{value:064x}"


def _slot_bytes32(value: str) -> str:
    """Encode one exact 32-byte value as an ABI word."""
    if not isinstance(value, str) or _BYTES32_RE.fullmatch(value) is None:
        raise ValueError(f"not a bytes32 value: {value!r}")
    return value[2:].lower()


def _topic_addr(addr: str) -> str:
    """Encode an EVM address as an indexed event topic."""
    return "0x" + _slot_addr(addr)


def _topic_bytes32(value: str) -> str:
    """Normalize a bytes32 value as an indexed event topic."""
    return "0x" + _slot_bytes32(value)


@dataclass
@final
class TokenView:
    """A strictly validated handle to a deployed EVM token."""

    rpc: RpcClient
    address: str

    def __post_init__(self) -> None:
        """Reject malformed token addresses when constructing the view."""
        _slot_addr(self.address)

    def balance_of(self, who: str, *, block: int | str = "latest") -> int:
        """Read an account balance from the token at a pinned block."""
        data = "0x" + SEL_BALANCE_OF + _slot_addr(who)
        return _word_to_int(self.rpc.eth_call(self.address, data, block), "balanceOf")

    def authorization_used(
        self, authorizer: str, nonce: str, *, block: int | str = "latest"
    ) -> bool:
        """Read and strictly decode EIP-3009 authorization consumption state."""
        data = "0x" + SEL_AUTHORIZATION_STATE + _slot_addr(authorizer) + _slot_bytes32(nonce)
        result = _word_to_int(self.rpc.eth_call(self.address, data, block), "authorizationState")
        if result not in {0, 1}:
            raise RpcError(f"authorizationState: expected ABI boolean, got {result}")
        return result == 1

    def event_mode(self, *, block: int | str = "latest") -> int:
        """Read the mock token's configured event mode at a pinned block."""
        return _word_to_int(
            self.rpc.eth_call(self.address, "0x" + SEL_EVENT_MODE, block), "eventMode"
        )

    def authorization_used_logs(
        self,
        *,
        authorizer: str | None = None,
        from_block: int | str = "earliest",
        to_block: int | str = "latest",
    ) -> list[dict[str, object]]:
        """Fetch authorization-consumption logs, optionally for one authorizer."""
        topics: list[str | None] = [TOPIC_AUTHORIZATION_USED]
        topics.append(_topic_addr(authorizer) if authorizer else None)
        return self.rpc.get_logs(
            address=self.address, topics=topics, from_block=from_block, to_block=to_block
        )

    def settle_calldata(
        self,
        *,
        from_addr: str,
        to: str,
        value: int,
        valid_after: int,
        valid_before: int,
        nonce: str,
        signature: str,
    ) -> str:
        """Encode EIP-3009 calldata only after validating every wire value."""
        from_slot = _slot_addr(from_addr)
        to_slot = _slot_addr(to)
        if int(from_addr[2:], 16) == 0 or int(to[2:], 16) == 0:
            raise ValueError("from_addr and to must be non-zero addresses")
        if type(value) is not int or not 1 <= value <= _UINT256_MAX:
            raise ValueError("value must be a positive uint256")
        if type(valid_after) is not int or not 0 <= valid_after <= _UINT256_MAX:
            raise ValueError("valid_after must be a uint256")
        if type(valid_before) is not int or not 1 <= valid_before <= _UINT256_MAX:
            raise ValueError("valid_before must be a positive uint256")
        if valid_after >= valid_before:
            raise ValueError("valid_after must be less than valid_before")
        nonce_slot = _slot_bytes32(nonce)
        if not isinstance(signature, str) or _SIGNATURE_RE.fullmatch(signature) is None:
            raise ValueError("signature must be exactly 65 bytes of 0x-prefixed hex")
        sig_hex = signature[2:].lower()
        head = (
            SEL_TRANSFER_WITH_AUTHORIZATION
            + from_slot
            + to_slot
            + _slot_uint(value)
            + _slot_uint(valid_after)
            + _slot_uint(valid_before)
            + nonce_slot
            + _slot_uint(7 * 32)
        )
        tail = _slot_uint(65) + sig_hex.ljust(3 * 64, "0")
        return "0x" + head + tail

    def set_event_mode_calldata(self, mode: int) -> str:
        """Encode validated calldata for selecting the mock token event mode."""
        if type(mode) is not int or mode not in {0, 1}:
            raise ValueError("mode must be 0 or 1")
        return "0x" + SEL_SET_EVENT_MODE + _slot_uint(mode)

    def set_fee_bps_calldata(self, bps: int) -> str:
        """Encode validated calldata for the mock token transfer fee."""
        if type(bps) is not int or not 0 <= bps <= 10_000:
            raise ValueError("bps must be an integer within [0, 10000]")
        return "0x" + SEL_SET_FEE_BPS + _slot_uint(bps)

    def mint_calldata(self, to: str, amount: int) -> str:
        """Encode validated mock-token mint calldata for local tests."""
        if type(amount) is not int or not 1 <= amount <= _UINT256_MAX:
            raise ValueError("amount must be a positive uint256")
        return "0x" + SEL_MINT + _slot_addr(to) + _slot_uint(amount)


@dataclass
@final
class SettlementTruth:
    """The on-chain ground truth about one attempted payment."""

    nonce_consumed: bool
    payer_balance_after: int
    payee_balance_after: int
    payer_delta: int
    payee_delta: int

    @property
    def funds_moved(self) -> bool:
        """The payment really happened on-chain (nonce burned + funds shifted)."""
        return self.nonce_consumed and self.payee_delta > 0 and self.payer_delta < 0
