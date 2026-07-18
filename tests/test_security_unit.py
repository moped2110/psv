"""Offline tests for the system-level security checks (C0 + N10)."""

from __future__ import annotations

import pytest

pytest.importorskip("eth_account")

from psv.payloads import EvmSigner, sign_authorization
from psv.security_checks import asset_matches, authorization_binds_to_chain, recovered_signer

PAYER_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
MERCHANT = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
TOKEN = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
HOME_CHAIN = 84532
OTHER_CHAIN = 1


def _auth(chain_id: int):
    signer = EvmSigner.from_key(PAYER_KEY)
    a = sign_authorization(
        signer=signer,
        to=MERCHANT,
        value=10_000,
        chain_id=chain_id,
        token_address=TOKEN,
        token_name="USDC",
        token_version="2",
        nonce="0x" + "cd" * 32,
    )
    return signer, a.as_dict()


def test_authorization_binds_to_its_own_chain() -> None:
    _signer, auth = _auth(HOME_CHAIN)
    assert (
        authorization_binds_to_chain(
            auth,
            expected_chain_id=HOME_CHAIN,
            token_address=TOKEN,
            token_name="USDC",
            token_version="2",
        )
        is True
    )


def test_cross_chain_replay_is_rejected() -> None:
    # Signed for OTHER_CHAIN, presented to a system on HOME_CHAIN: the recovered
    # address won't match `from`, so the binding check fails -> reject pre-flight.
    _signer, auth = _auth(OTHER_CHAIN)
    assert (
        authorization_binds_to_chain(
            auth,
            expected_chain_id=HOME_CHAIN,
            token_address=TOKEN,
            token_name="USDC",
            token_version="2",
        )
        is False
    )


def test_recovered_signer_differs_across_chains() -> None:
    signer, auth = _auth(HOME_CHAIN)
    same = recovered_signer(
        auth, chain_id=HOME_CHAIN, token_address=TOKEN, token_name="USDC", token_version="2"
    )
    other = recovered_signer(
        auth, chain_id=OTHER_CHAIN, token_address=TOKEN, token_name="USDC", token_version="2"
    )
    assert same == signer.address
    assert other != signer.address  # wrong-chain recovery yields a different addr


def test_asset_scoping_rejects_fake_token() -> None:
    fake = "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    assert asset_matches(TOKEN, TOKEN) is True
    assert asset_matches(fake, TOKEN) is False  # a worthless token must not count
    assert asset_matches(TOKEN.lower(), TOKEN.upper()) is True  # case-insensitive


def test_asset_scoping_rejects_malformed_addresses() -> None:
    with pytest.raises(ValueError):
        asset_matches("garbage", TOKEN)


def test_binds_to_chain_false_on_unrecoverable_signature() -> None:
    # A malformed signature makes recovery raise; the guard must fail closed
    # (return False), never leak the exception to the caller.
    _signer, auth = _auth(HOME_CHAIN)
    auth["signature"] = "0x1234"  # too short to recover -> raises internally
    assert (
        authorization_binds_to_chain(
            auth,
            expected_chain_id=HOME_CHAIN,
            token_address=TOKEN,
            token_name="USDC",
            token_version="2",
        )
        is False
    )
