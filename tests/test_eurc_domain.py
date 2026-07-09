"""EURC-style domain: EIP-712 domain separation blocks cross-token replay.

The signing domain binds an authorization to a specific token (name + version +
verifying contract). A signature produced for one token must not validate as a
transfer of another, even when from/to/value/nonce are identical — otherwise a
USDC authorization could be replayed against a EURC contract. We prove the signed
digest is domain-bound and that recovery under a foreign token domain yields a
different signer.
"""

from __future__ import annotations

import pytest

pytest.importorskip("eth_account")

from eth_account import Account
from eth_account.messages import encode_typed_data

from psv.payloads import (
    _TRANSFER_WITH_AUTHORIZATION_TYPES,
    EvmSigner,
    _domain,
    _message,
    eip712_digest,
    sign_authorization,
)

CHAIN_ID = 84532
TOKEN = "0x" + "22" * 20
SIGNER = EvmSigner.from_key("0x" + "77" * 32)

# (name, version) pairs for two different tokens sharing one verifying contract
# address — isolating the domain *name/version* as the discriminator.
USDC = ("USD Coin", "2")
EURC = ("EURC", "1")


def _auth_dict() -> tuple[dict[str, str], str]:
    auth = sign_authorization(
        signer=SIGNER,
        to="0x" + "33" * 20,
        value=1000,
        chain_id=CHAIN_ID,
        token_address=TOKEN,
        token_name=USDC[0],
        token_version=USDC[1],
    )
    return {
        "from": auth.from_addr,
        "to": auth.to,
        "value": str(auth.value),
        "validAfter": str(auth.valid_after),
        "validBefore": str(auth.valid_before),
        "nonce": auth.nonce,
    }, auth.signature


def _recover(auth: dict[str, str], name: str, version: str, signature: str) -> str:
    signable = encode_typed_data(
        _domain(CHAIN_ID, TOKEN, name, version),
        _TRANSFER_WITH_AUTHORIZATION_TYPES,
        _message(auth),
    )
    return Account.recover_message(signable, signature=signature)


def test_digest_is_domain_bound() -> None:
    auth, _sig = _auth_dict()
    # Same authorization, different token domain → different signed digest.
    assert eip712_digest(auth, CHAIN_ID, TOKEN, *USDC) != eip712_digest(
        auth, CHAIN_ID, TOKEN, *EURC
    )


def test_signature_does_not_cross_token_domains() -> None:
    auth, sig = _auth_dict()
    # Recovered under the correct (USDC) domain → the real signer.
    assert _recover(auth, *USDC, sig).lower() == SIGNER.address.lower()
    # Recovered under a EURC domain → a different address, so the USDC signature
    # is NOT a valid EURC transfer authorization: no cross-token replay.
    assert _recover(auth, *EURC, sig).lower() != SIGNER.address.lower()
