"""System-level security checks (Phase 4, C/N classes).

  * **C0 - cross-chain signature replay.** An EIP-3009 authorization is bound to a
    ``chainId`` through the EIP-712 domain. Replayed against a system on a
    different chain it must not recover to the signer (and the token reverts
    on-chain). ``authorization_binds_to_chain`` lets a SUT reject - pre-flight -
    an authorization whose domain doesn't match its own chain.
  * **N10 - fake-token / whitelist bypass.** Settlement must be verified against
    the EXPECTED asset contract, not "any Transfer to the merchant". A system that
    doesn't scope its event scan by token address is fooled by a worthless
    attacker-deployed token. ``asset_matches`` is the guard.
  * **N15 - session / order-id predictability.** If order ids are guessable, an
    attacker can enumerate them and claim someone else's paid resource. Ids must
    carry enough unpredictable entropy; ``sufficient_id_entropy`` checks it.

The recovery uses the same independent EIP-712 assembly as ``psv.payloads`` (no
x402 SDK), so it is offline-testable.
"""

from __future__ import annotations

import re
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
    a different address here, so this returns ``False`` - the SUT should reject it
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


_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


def sufficient_id_entropy(order_id: str, *, prefix: str = "ord_", min_random_hex: int = 12) -> bool:
    """True iff ``order_id`` carries enough unpredictable hex after its prefix.

    Sequential or short ids (``ord_1``, ``ord_42``) are guessable: an attacker can
    enumerate them and try to claim another customer's paid resource. A random id
    of >= ``min_random_hex`` hex chars (e.g. ``secrets.token_hex(8)`` = 16) is not.
    """
    body = order_id[len(prefix):] if order_id.startswith(prefix) else order_id
    return len(body) >= min_random_hex and bool(_HEX_RE.match(body))


def asset_is_deployed_contract(code: str) -> bool:
    """True iff ``code`` (an ``eth_getCode`` result) is non-empty deployed bytecode.

    The EVM does not revert when a function is called on an address with no code
    (an EOA): ``eth_call`` returns empty data and an on-chain
    ``transferWithAuthorization`` is a *silent no-op* - it "succeeds" but moves
    nothing and emits no ``Transfer``. A system that settles against such an asset
    believes it was paid while the chain shows nothing moved - a PHANTOM_CREDIT
    divergence. The robust guard is a pre-flight ``eth_getCode`` on the asset:
    empty bytecode -> reject the asset before signature verification or settlement.
    Mirrors the x402 SDK's ``asset_not_deployed_contract`` check (x402#2554).
    """
    stripped = code.lower().removeprefix("0x")
    return len(stripped) > 0 and any(c != "0" for c in stripped)
