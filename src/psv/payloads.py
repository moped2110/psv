"""EIP-3009 ``TransferWithAuthorization`` signing — independent of any x402 SDK.

Lifted from ``01-x402-testsuite/payload_builder.py`` (proven byte-identical to
the reference SDK there). Kept self-contained so the harness can sign payments
to drive the SUT without importing the sibling project. No mainnet keys, ever —
signers are local Anvil/throwaway keys.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

try:
    from eth_account import Account
    from eth_account.messages import _hash_eip191_message, encode_typed_data
    from eth_account.signers.local import LocalAccount

    _EVM_AVAILABLE = True
except ImportError:  # pragma: no cover - only without the [chain] extra
    _EVM_AVAILABLE = False


_TRANSFER_WITH_AUTHORIZATION_TYPES: dict[str, list[dict[str, str]]] = {
    "TransferWithAuthorization": [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"},
    ]
}


def _require_evm() -> None:
    if not _EVM_AVAILABLE:
        raise RuntimeError(
            "EIP-3009 signing requires eth-account. Install with: pip install psv[chain]"
        )


@dataclass
class EvmSigner:
    """A local/throwaway EVM signer wrapping an eth-account LocalAccount."""

    account: "LocalAccount"

    @classmethod
    def from_key(cls, private_key: str) -> "EvmSigner":
        _require_evm()
        return cls(Account.from_key(private_key))

    @classmethod
    def random(cls) -> "EvmSigner":
        _require_evm()
        return cls(Account.from_key("0x" + secrets.token_hex(32)))

    @property
    def address(self) -> str:
        return str(self.account.address)


def _domain(chain_id: int, verifying_contract: str, name: str, version: str) -> dict[str, Any]:
    return {
        "name": name,
        "version": version,
        "chainId": chain_id,
        "verifyingContract": verifying_contract,
    }


def _message(authorization: dict[str, Any]) -> dict[str, Any]:
    return {
        "from": authorization["from"],
        "to": authorization["to"],
        "value": int(authorization["value"]),
        "validAfter": int(authorization["validAfter"]),
        "validBefore": int(authorization["validBefore"]),
        "nonce": bytes.fromhex(str(authorization["nonce"]).removeprefix("0x")),
    }


def eip712_digest(
    authorization: dict[str, Any],
    chain_id: int,
    verifying_contract: str,
    token_name: str,
    token_version: str,
) -> bytes:
    """The EIP-712 digest signed for an EIP-3009 authorization."""
    _require_evm()
    signable = encode_typed_data(
        _domain(chain_id, verifying_contract, token_name, token_version),
        _TRANSFER_WITH_AUTHORIZATION_TYPES,
        _message(authorization),
    )
    return bytes(_hash_eip191_message(signable))


@dataclass
class Authorization:
    """A signed EIP-3009 transfer authorization, ready to settle on-chain."""

    from_addr: str
    to: str
    value: int
    valid_after: int
    valid_before: int
    nonce: str  # 0x-prefixed 32-byte hex
    signature: str  # 0x-prefixed 65-byte hex

    def as_dict(self) -> dict[str, Any]:
        return {
            "from": self.from_addr,
            "to": self.to,
            "value": str(self.value),
            "validAfter": str(self.valid_after),
            "validBefore": str(self.valid_before),
            "nonce": self.nonce,
            "signature": self.signature,
        }


def sign_authorization(
    *,
    signer: EvmSigner,
    to: str,
    value: int,
    chain_id: int,
    token_address: str,
    token_name: str,
    token_version: str,
    valid_after: int = 0,
    valid_before: int = 2**48,
    nonce: str | None = None,
) -> Authorization:
    """Sign a valid EIP-3009 authorization paying ``value`` to ``to``.

    Defaults give a wide validity window so settlement is purely about the
    signature/nonce, not timing — timing is exercised elsewhere.
    """
    _require_evm()
    nonce = nonce if nonce is not None else "0x" + secrets.token_hex(32)
    authorization = {
        "from": signer.address,
        "to": to,
        "value": str(value),
        "validAfter": str(valid_after),
        "validBefore": str(valid_before),
        "nonce": nonce,
    }
    signable = encode_typed_data(
        _domain(chain_id, token_address, token_name, token_version),
        _TRANSFER_WITH_AUTHORIZATION_TYPES,
        _message(authorization),
    )
    signed = signer.account.sign_message(signable)
    return Authorization(
        from_addr=signer.address,
        to=to,
        value=value,
        valid_after=valid_after,
        valid_before=valid_before,
        nonce=nonce,
        signature=signed.signature.to_0x_hex(),
    )
