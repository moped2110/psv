"""N16 — asset is an EOA (silent no-op / payment bypass). On-chain, against Anvil.

The EVM does not revert when a function is called on an address with no code:
sending the ``transferWithAuthorization`` calldata to an EOA "succeeds" (status 1)
but moves nothing and emits no event. A system that points its asset at an EOA —
or trusts the tx-receipt status as proof of payment — is phantom-credited: it
believes it was paid while the chain shows nothing moved.

This reproduces the exact failure mode the x402 SDK closed in #2554 (the
``asset_not_deployed_contract`` guard): an ``eth_getCode`` pre-flight rejects an
asset with no bytecode before settling. We prove (a) the chain genuinely lets the
silent no-op through, and (b) the guard distinguishes the EOA from the real token.

Run: pytest -m onchain tests/test_n16_eoa_asset_silent_noop.py
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import ANVIL_ACCOUNTS, DEFAULT_CHAIN_ID, DEFAULT_TOKEN, send_tx

from psv.chain import TokenView
from psv.payloads import EvmSigner, sign_authorization
from psv.security_checks import asset_is_deployed_contract

pytestmark = pytest.mark.onchain

# A standard Anvil dev account (#4): funded with ETH but holding NO contract code
# — a wallet posing as a token, the definition of the N16 trap.
_EOA_ASSET = "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65"


def test_eth_getcode_distinguishes_eoa_from_token(rpc: Any, funded_token: TokenView) -> None:
    # The real token has bytecode -> the guard accepts it (no false positive).
    assert asset_is_deployed_contract(rpc.get_code(DEFAULT_TOKEN)) is True
    # The EOA has none -> the guard rejects it: the pre-flight that prevents the no-op.
    assert asset_is_deployed_contract(rpc.get_code(_EOA_ASSET)) is False


def test_settling_against_an_eoa_is_a_silent_noop(rpc: Any, funded_token: TokenView) -> None:
    token = funded_token
    payer = EvmSigner.from_key(ANVIL_ACCOUNTS["payer"][1])
    merchant = ANVIL_ACCOUNTS["merchant"][0]
    amount = 10_000

    payer_before = token.balance_of(payer.address)
    merchant_before = token.balance_of(merchant)

    # Sign an authorization whose verifyingContract is the EOA, then send the
    # transferWithAuthorization calldata TO that EOA — exactly what a system that
    # trusts a client-supplied asset (without an eth_getCode pre-flight) would do.
    auth = sign_authorization(
        signer=payer,
        to=merchant,
        value=amount,
        chain_id=DEFAULT_CHAIN_ID,
        token_address=_EOA_ASSET,
        token_name="USDC",
        token_version="2",
    )
    calldata = TokenView(rpc=rpc, address=_EOA_ASSET).settle_calldata(
        from_addr=payer.address,
        to=merchant,
        value=amount,
        valid_after=auth.valid_after,
        valid_before=auth.valid_before,
        nonce=auth.nonce,
        signature=auth.signature,
    )
    tx_hash = send_tx(rpc, ANVIL_ACCOUNTS["deployer"][1], _EOA_ASSET, calldata, DEFAULT_CHAIN_ID)
    receipt = rpc.call("eth_getTransactionReceipt", [tx_hash])

    # The call to an EOA does NOT revert — status success — yet nothing happened.
    assert receipt is not None
    assert int(receipt["status"], 16) == 1  # tx "succeeded"
    assert token.balance_of(payer.address) == payer_before  # but no funds moved
    assert token.balance_of(merchant) == merchant_before
    assert receipt["logs"] == []  # no Transfer / AuthorizationUsed

    # A system trusting that receipt would now phantom-credit the payer. The
    # eth_getCode guard is exactly what prevents reaching this point.
    assert asset_is_deployed_contract(rpc.get_code(_EOA_ASSET)) is False
