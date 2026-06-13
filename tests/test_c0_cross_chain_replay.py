"""C0 — cross-chain signature replay (Phase 4). On-chain, against Anvil.

An EIP-3009 authorization signed for a *different* chain is presented to the
system. The EIP-712 domain binds the signature to ``chainId``, so the token's
on-chain recovery fails and the settlement does not go through — no funds move,
the nonce is untouched. The harness also catches it pre-flight with
``authorization_binds_to_chain``.

Run: pytest -m onchain tests/test_c0_cross_chain_replay.py
"""

from __future__ import annotations

from typing import Any

import pytest

from psv.chain import TokenView
from psv.payloads import EvmSigner, sign_authorization
from psv.reference_sut.server import ReferenceSut, SutConfig
from psv.security_checks import authorization_binds_to_chain

from conftest import ANVIL_ACCOUNTS, DEFAULT_CHAIN_ID, DEFAULT_RPC, DEFAULT_TOKEN, send_tx

pytestmark = pytest.mark.onchain

FOREIGN_CHAIN_ID = 1  # signed for mainnet, presented to the Base-Sepolia system


def test_cross_chain_replayed_authorization_does_not_settle(
    rpc: Any, funded_token: TokenView
) -> None:
    token = funded_token
    send_tx(rpc, ANVIL_ACCOUNTS["deployer"][1], DEFAULT_TOKEN,
            token.set_event_mode_calldata(0), DEFAULT_CHAIN_ID)
    sut = ReferenceSut(
        SutConfig(
            token_address=DEFAULT_TOKEN, merchant_address=ANVIL_ACCOUNTS["merchant"][0],
            facilitator_key=ANVIL_ACCOUNTS["deployer"][1], chain_id=DEFAULT_CHAIN_ID,
            rpc_endpoint=DEFAULT_RPC,
        )
    )
    payer = EvmSigner.from_key(ANVIL_ACCOUNTS["payer"][1])
    merchant = ANVIL_ACCOUNTS["merchant"][0]

    quote = sut.quote()
    amount = int(quote["amount"])
    payer_before = token.balance_of(payer.address)
    merchant_before = token.balance_of(merchant)

    # Sign for the FOREIGN chain — a replay against this system.
    auth = sign_authorization(
        signer=payer, to=merchant, value=amount, chain_id=FOREIGN_CHAIN_ID,
        token_address=DEFAULT_TOKEN, token_name="USDC", token_version="2",
    )

    # Pre-flight: the harness rejects it (recovers to a different address here).
    assert authorization_binds_to_chain(
        auth.as_dict(), expected_chain_id=DEFAULT_CHAIN_ID, token_address=DEFAULT_TOKEN,
        token_name="USDC", token_version="2",
    ) is False

    # On-chain: the token reverts (or the send is rejected) — either way, the
    # settlement cannot go through.
    try:
        result = sut.pay(quote["order_id"], auth.as_dict())
        assert result["settled"] is False
    except Exception:
        pass  # a pre-mine rejection is an equally valid defense outcome

    # Nothing moved; the nonce is still free.
    assert token.balance_of(payer.address) == payer_before
    assert token.balance_of(merchant) == merchant_before
    assert token.authorization_used(payer.address, auth.nonce) is False
