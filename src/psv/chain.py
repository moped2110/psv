"""Chain-truth oracle + on-chain helpers for the UpgradeableMockUSDC token.

This module is the harness's **independent source of truth**. It reads the chain
directly - balances, the EIP-3009 nonce state, and the drift-proof
``AuthorizationUsed`` event - to establish what *actually* happened on-chain,
without trusting the SUT or the facilitator.

Settlement truth is derived from ``AuthorizationUsed(authorizer, nonce)`` and the
balance delta, **not** from the ``Transfer`` event - the whole point of SC1 is
that the ``Transfer`` event signature can drift, blinding a system that watches
it, while ``AuthorizationUsed`` and balances do not.

ABI encoding is done by hand (fixed-width slots) to keep the core dependency on
``web3`` optional and the logic unit-testable against a fake transport.
"""

from __future__ import annotations

from dataclasses import dataclass

from .anvil import RpcClient

# Function selectors (first 4 bytes of keccak(signature)).
SEL_BALANCE_OF = "70a08231"
SEL_AUTHORIZATION_STATE = "e94a0102"
SEL_TRANSFER_WITH_AUTHORIZATION = "cf092995"
SEL_SET_EVENT_MODE = "2a030f44"
SEL_SET_FEE_BPS = "023b1fc9"
SEL_MINT = "40c10f19"
SEL_EVENT_MODE = "0ce978e2"

# Event topic0 hashes (keccak of the event signature).
TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TOPIC_TRANSFER_V2 = "0x58f9acac7a1c69c84dff9a713e28686566926c704ffaaa5562e8225bdf50911b"
TOPIC_AUTHORIZATION_USED = "0x98de503528ee59b575ef0c0a2576a82497bfc029a5685b209e9ec333479b10a5"


def _slot_addr(addr: str) -> str:
    """Left-pad a 20-byte address to a 32-byte ABI slot (hex, no 0x)."""
    return addr.lower().removeprefix("0x").rjust(64, "0")


def _slot_uint(value: int) -> str:
    return f"{value:064x}"


def _slot_bytes32(b: str) -> str:
    return b.lower().removeprefix("0x").rjust(64, "0")


def _topic_addr(addr: str) -> str:
    return "0x" + _slot_addr(addr)


def _topic_bytes32(b: str) -> str:
    return "0x" + _slot_bytes32(b)


@dataclass
class TokenView:
    """A read/write handle to a deployed UpgradeableMockUSDC at ``address``."""

    rpc: RpcClient
    address: str

    # --- ground-truth reads ---------------------------------------------------

    def balance_of(self, who: str) -> int:
        data = "0x" + SEL_BALANCE_OF + _slot_addr(who)
        return int(self.rpc.eth_call(self.address, data), 16)

    def authorization_used(self, authorizer: str, nonce: str) -> bool:
        data = "0x" + SEL_AUTHORIZATION_STATE + _slot_addr(authorizer) + _slot_bytes32(nonce)
        return int(self.rpc.eth_call(self.address, data), 16) == 1

    def event_mode(self) -> int:
        return int(self.rpc.eth_call(self.address, "0x" + SEL_EVENT_MODE), 16)

    def authorization_used_logs(
        self, *, authorizer: str | None = None, from_block: int | str = "earliest"
    ) -> list[dict[str, object]]:
        """Drift-proof settlement evidence: ``AuthorizationUsed`` log entries."""
        topics: list[str | None] = [TOPIC_AUTHORIZATION_USED]
        topics.append(_topic_addr(authorizer) if authorizer else None)
        return self.rpc.get_logs(address=self.address, topics=topics, from_block=from_block)

    # --- writes (facilitator / admin roles; need a funded sender) -------------

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
        """ABI-encoded ``transferWithAuthorization`` calldata (a `bytes` tail)."""
        sig_hex = signature.lower().removeprefix("0x")
        sig_len = len(sig_hex) // 2
        head = (
            SEL_TRANSFER_WITH_AUTHORIZATION
            + _slot_addr(from_addr)
            + _slot_addr(to)
            + _slot_uint(value)
            + _slot_uint(valid_after)
            + _slot_uint(valid_before)
            + _slot_bytes32(nonce)
            + _slot_uint(7 * 32)  # offset to the bytes arg (7 head words precede it)
        )
        tail = _slot_uint(sig_len) + sig_hex.ljust(((sig_len + 31) // 32) * 64, "0")
        return "0x" + head + tail

    def set_event_mode_calldata(self, mode: int) -> str:
        return "0x" + SEL_SET_EVENT_MODE + _slot_uint(mode)

    def set_fee_bps_calldata(self, bps: int) -> str:
        return "0x" + SEL_SET_FEE_BPS + _slot_uint(bps)

    def mint_calldata(self, to: str, amount: int) -> str:
        return "0x" + SEL_MINT + _slot_addr(to) + _slot_uint(amount)


@dataclass
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
