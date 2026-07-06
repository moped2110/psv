"""Offline unit tests for EIP-3009 signing (no chain).

The strongest offline check: sign an authorization, then independently recover the
signer's address from the signature. If it matches, the EIP-712 domain, types and
message were assembled correctly — proving signing correctness without any chain.
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

# Anvil dev account #1 (public, test-only).
PAYER_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
PAYER_ADDR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
MERCHANT = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
TOKEN = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
CHAIN_ID = 84532


def _signed(**kw):
    signer = EvmSigner.from_key(PAYER_KEY)
    auth = sign_authorization(
        signer=signer,
        to=MERCHANT,
        value=10_000,
        chain_id=CHAIN_ID,
        token_address=TOKEN,
        token_name="USDC",
        token_version="2",
        nonce="0x" + "ab" * 32,
        **kw,
    )
    return signer, auth


def test_address_from_known_key() -> None:
    assert EvmSigner.from_key(PAYER_KEY).address == PAYER_ADDR


def test_signature_recovers_to_signer() -> None:
    signer, auth = _signed()
    # Rebuild the exact signable message and recover the address from the signature.
    signable = encode_typed_data(
        _domain(CHAIN_ID, TOKEN, "USDC", "2"),
        _TRANSFER_WITH_AUTHORIZATION_TYPES,
        _message(auth.as_dict()),
    )
    recovered = Account.recover_message(signable, signature=auth.signature)
    assert recovered == signer.address


def test_wrong_domain_does_not_recover_to_signer() -> None:
    # A signature is bound to its domain: recovering against a different chainId
    # must NOT yield the signer (this is the cross-chain replay defense).
    signer, auth = _signed()
    signable = encode_typed_data(
        _domain(1, TOKEN, "USDC", "2"),  # wrong chainId
        _TRANSFER_WITH_AUTHORIZATION_TYPES,
        _message(auth.as_dict()),
    )
    recovered = Account.recover_message(signable, signature=auth.signature)
    assert recovered != signer.address


def test_digest_is_deterministic_and_32_bytes() -> None:
    _signer, auth = _signed()
    d1 = eip712_digest(auth.as_dict(), CHAIN_ID, TOKEN, "USDC", "2")
    d2 = eip712_digest(auth.as_dict(), CHAIN_ID, TOKEN, "USDC", "2")
    assert d1 == d2
    assert isinstance(d1, bytes) and len(d1) == 32


def test_as_dict_shape_and_types() -> None:
    _signer, auth = _signed()
    d = auth.as_dict()
    assert set(d) == {"from", "to", "value", "validAfter", "validBefore", "nonce", "signature"}
    assert d["from"] == PAYER_ADDR and d["to"] == MERCHANT
    assert d["value"] == "10000"  # stringified atomic amount
    assert d["nonce"] == "0x" + "ab" * 32
    assert d["signature"].startswith("0x") and len(d["signature"]) == 2 + 130  # 65 bytes


def test_random_signers_are_distinct() -> None:
    a, b = EvmSigner.random(), EvmSigner.random()
    assert a.address != b.address
    assert a.address.startswith("0x") and len(a.address) == 42


def test_custom_window_is_carried_through() -> None:
    _signer, auth = _signed(valid_after=100, valid_before=200)
    d = auth.as_dict()
    assert d["validAfter"] == "100" and d["validBefore"] == "200"
