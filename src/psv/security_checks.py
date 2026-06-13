"""System-level security checks (Phase 4, C/N classes).

  * **C0 — cross-chain signature replay.** An EIP-3009 authorization is bound to a
    ``chainId`` through the EIP-712 domain. Replayed against a system on a
    different chain it must not recover to the signer (and the token reverts
    on-chain). ``authorization_binds_to_chain`` lets a SUT reject — pre-flight —
    an authorization whose domain doesn't match its own chain.
  * **N10 — fake-token / whitelist bypass.** Settlement must be verified against
    the EXPECTED asset contract, not "any Transfer to the merchant". A system that
    doesn't scope its event scan by token address is fooled by a worthless
    attacker-deployed token. ``asset_matches`` is the guard.

The recovery uses the same independent EIP-712 assembly as ``psv.payloads`` (no
x402 SDK), so it is offline-testable.
"""

from __future__ import annotations

from typing import Any

from .payloads import (
    _TRANSFER_WITH_AUTHORIZATION_TYPES,
    _domain,
    _message,
    _require_evm,
)


def recovered_signer(
    auth: dict[str, Any],
    *,
    chain_id: int,
    token_address: str,
    token_name: str,
    token_version: str,
) -> str:
    """Recover the address that signed ``auth`` under the given EIP-712 domain."""
    _require_evm()
    from eth_account import Account
    from eth_account.messages import encode_typed_data

    signable = encode_typed_data(
        _domain(chain_id, token_address, token_name, token_version),
        _TRANSFER_WITH_AUTHORIZATION_TYPES,
        _message(auth),
    )
    return str(Account.recover_message(signable, signature=auth["signature"]))


def authorization_binds_to_chain(
    auth: dict[str, Any],
    *,
    expected_chain_id: int,
    token_address: str,
    token_name: str,
    token_version: str,
) -> bool:
    """True iff ``auth`` recovers to its claimed ``from`` under ``expected_chain_id``.

    A cross-chain-replayed authorization (signed for a different chain) recovers to
    a different address here, so this returns ``False`` — the SUT should reject it
    before ever submitting the (reverting) settlement.
    """
    try:
        recovered = recovered_signer(
            auth, chain_id=expected_chain_id, token_address=token_address,
            token_name=token_name, token_version=token_version,
        )
    except Exception:
        return False
    return recovered.lower() == str(auth["from"]).lower()


def asset_matches(log_address: str, expected_token: str) -> bool:
    """True iff a settlement log came from the expected token contract."""
    return log_address.lower() == expected_token.lower()
