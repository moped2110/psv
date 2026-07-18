"""Fail-closed safety policy for every value-bearing reference-SUT submission.

The reference SUT is a test harness, not a production wallet.  It may submit
transactions only to a deliberately small local/testnet allowlist.  There is no
runtime override: adding a chain is a reviewed source change with regression
tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class SettlementSafetyError(RuntimeError):
    """A settlement was refused before transaction construction or signing."""


class SafetyRpc(Protocol):
    """The read-only RPC surface needed for the pre-signing decision."""

    def call(self, method: str, params: list[object] | None = None) -> object:
        """Call a read-only JSON-RPC method required by the policy."""
        ...

    def get_code(self, address: str, block: str = "latest") -> str:
        """Read deployed code for a policy-validated token address."""
        ...


# Local developer chains plus the two explicitly supported public EVM testnets.
# Mainnets and every unknown chain remain denied without an override mechanism.
ALLOWED_SETTLEMENT_CHAIN_IDS: frozenset[int] = frozenset({1337, 31337, 84532, 11155111})


def _require_address(value: str, *, field: str) -> str:
    """Validate, normalize, and reject the zero EVM address."""
    hex_value = value.removeprefix("0x")
    if len(hex_value) != 40:
        raise SettlementSafetyError(f"{field} must be an exact 20-byte EVM address")
    try:
        numeric = int(hex_value, 16)
    except ValueError as exc:
        raise SettlementSafetyError(f"{field} must be a hexadecimal EVM address") from exc
    if numeric == 0:
        raise SettlementSafetyError(f"{field} must not be the zero address")
    return "0x" + hex_value.lower()


def _parse_chain_id(raw: object) -> int:
    """Strictly decode a positive chain identifier from JSON-RPC."""
    if not isinstance(raw, str) or not raw.startswith("0x"):
        raise SettlementSafetyError("eth_chainId returned a malformed result")
    try:
        chain_id = int(raw, 16)
    except ValueError as exc:
        raise SettlementSafetyError("eth_chainId returned a malformed result") from exc
    if chain_id <= 0:
        raise SettlementSafetyError("eth_chainId returned an invalid chain id")
    return chain_id


def _require_deployed_code(code: str) -> None:
    """Require well-formed, non-zero deployed contract bytecode."""
    if not isinstance(code, str) or not code.startswith("0x"):
        raise SettlementSafetyError("eth_getCode returned a malformed result")
    hex_code = code[2:]
    if not hex_code:
        raise SettlementSafetyError("configured token address has no deployed contract code")
    try:
        numeric = int(hex_code, 16)
    except ValueError as exc:
        raise SettlementSafetyError("eth_getCode returned a malformed result") from exc
    if numeric == 0:
        raise SettlementSafetyError("configured token address has no deployed contract code")
    if len(hex_code) % 2:
        raise SettlementSafetyError("eth_getCode returned a malformed result")


def _require_exact_amount(raw: object, *, expected: int) -> None:
    """Require an integer authorization amount equal to the quoted amount."""
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise SettlementSafetyError("authorization amount must be an integer")
    if isinstance(raw, str) and (not raw or not raw.isascii() or not raw.isdecimal()):
        raise SettlementSafetyError("authorization amount must be an integer")
    amount = int(raw)
    if expected <= 0 or expected >= 2**256:
        raise SettlementSafetyError("configured order amount is outside uint256 payment bounds")
    if amount != expected:
        raise SettlementSafetyError("authorization amount does not match the quoted order amount")


@dataclass(frozen=True)
class SettlementSafetyPolicy:
    """Central, non-overridable test-chain policy used at submission time."""

    def require_safe_submission(
        self,
        *,
        rpc: SafetyRpc,
        configured_chain_id: int,
        token_address: str,
        payer_address: str,
        payee_address: str,
        authorization_to: str,
        authorization_amount: object,
        expected_amount: int,
    ) -> None:
        """Fail closed unless chain, token, parties, and amount are safe to submit."""
        if configured_chain_id not in ALLOWED_SETTLEMENT_CHAIN_IDS:
            raise SettlementSafetyError(
                f"chain eip155:{configured_chain_id} is not in the local/testnet allowlist"
            )

        actual_chain_id = _parse_chain_id(rpc.call("eth_chainId"))
        if actual_chain_id != configured_chain_id:
            raise SettlementSafetyError(
                "RPC chain mismatch: configured "
                f"eip155:{configured_chain_id}, RPC returned eip155:{actual_chain_id}"
            )

        token = _require_address(token_address, field="token address")
        _require_address(payer_address, field="payer address")
        payee = _require_address(payee_address, field="payee address")
        authorized_payee = _require_address(authorization_to, field="authorization payee")
        if authorized_payee != payee:
            raise SettlementSafetyError(
                "authorization payee does not match the configured merchant"
            )

        _require_exact_amount(authorization_amount, expected=expected_amount)

        _require_deployed_code(rpc.get_code(token))


DEFAULT_SETTLEMENT_SAFETY_POLICY = SettlementSafetyPolicy()
