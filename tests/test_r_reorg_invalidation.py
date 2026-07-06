"""R-class — reorg invalidation (Phase 2). On-chain, against Anvil.

A payment settles and the SUT books it as paid. Then a chain reorg drops the
settlement block: the funds return to the payer and the EIP-3009 nonce is free
again — but the SUT still believes the order is paid. The harness catches the
resulting **phantom credit**, which a black-box tester (and the SUT itself,
trusting first inclusion) cannot see. The fix is finality-by-confirmations.

Run: pytest -m onchain tests/test_r_reorg_invalidation.py
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import ANVIL_ACCOUNTS, DEFAULT_CHAIN_ID, DEFAULT_RPC, DEFAULT_TOKEN, send_tx

from psv.chain import TokenView
from psv.divergence import DivergenceKind, detect_payment_divergence, settlement_truth_from_balances
from psv.payloads import EvmSigner, sign_authorization
from psv.reference_sut.server import ReferenceSut, SutConfig
from psv.reorg import confirmations, is_final, reorg_to, take_checkpoint

pytestmark = pytest.mark.onchain

REQUIRED_CONFIRMATIONS = 5


def _sut() -> ReferenceSut:
    return ReferenceSut(
        SutConfig(
            token_address=DEFAULT_TOKEN,
            merchant_address=ANVIL_ACCOUNTS["merchant"][0],
            facilitator_key=ANVIL_ACCOUNTS["deployer"][1],
            chain_id=DEFAULT_CHAIN_ID,
            rpc_endpoint=DEFAULT_RPC,
        )
    )


def test_reorg_undoes_settlement_but_sut_keeps_phantom_credit(
    rpc: Any, funded_token: TokenView
) -> None:
    token = funded_token
    send_tx(
        rpc,
        ANVIL_ACCOUNTS["deployer"][1],
        DEFAULT_TOKEN,
        token.set_event_mode_calldata(0),
        DEFAULT_CHAIN_ID,
    )
    sut = _sut()
    payer = EvmSigner.from_key(ANVIL_ACCOUNTS["payer"][1])
    merchant = ANVIL_ACCOUNTS["merchant"][0]

    quote = sut.quote()
    amount = int(quote["amount"])
    payer_pre = token.balance_of(payer.address)
    merchant_pre = token.balance_of(merchant)

    # Checkpoint the chain BEFORE settling, so we can reorg the settlement away.
    checkpoint = take_checkpoint(rpc)

    auth = sign_authorization(
        signer=payer,
        to=merchant,
        value=amount,
        chain_id=DEFAULT_CHAIN_ID,
        token_address=DEFAULT_TOKEN,
        token_name="USDC",
        token_version="2",
    )
    result = sut.pay(quote["order_id"], auth.as_dict())
    assert result["settled"] is True
    assert sut.status(quote["order_id"])["paid"] is True

    # Pre-reorg: the settlement really happened, but it is shallow (not final).
    assert token.balance_of(payer.address) == payer_pre - amount
    assert token.authorization_used(payer.address, auth.nonce) is True
    receipt = rpc.call("eth_getTransactionReceipt", [result["submitted_tx"]])
    tx_block = int(receipt["blockNumber"], 16)
    assert confirmations(rpc.block_number(), tx_block) >= 1
    assert is_final(rpc.block_number(), tx_block, REQUIRED_CONFIRMATIONS) is False

    # The reorg: drop the settlement block.
    assert reorg_to(rpc, checkpoint) is True

    # Post-reorg chain truth: funds returned, nonce free again.
    payer_post = token.balance_of(payer.address)
    merchant_post = token.balance_of(merchant)
    assert payer_post == payer_pre
    assert merchant_post == merchant_pre
    assert token.authorization_used(payer.address, auth.nonce) is False

    truth = settlement_truth_from_balances(
        nonce_consumed=token.authorization_used(payer.address, auth.nonce),
        payer_before=payer_pre,
        payer_after=payer_post,
        payee_before=merchant_pre,
        payee_after=merchant_post,
    )
    assert truth.funds_moved is False

    # The SUT still believes it was paid -> the harness flags a phantom credit.
    divergence = detect_payment_divergence(
        truth, sut_believes_paid=sut.status(quote["order_id"])["paid"]
    )
    assert divergence.kind is DivergenceKind.PHANTOM_CREDIT
    assert divergence.is_failure
