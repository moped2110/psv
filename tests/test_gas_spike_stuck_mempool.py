"""Gas spike / stuck mempool — a late-mined settlement. On-chain, against Anvil.

Congestion (a gas spike) can leave a settlement transaction pending well past the
moment a system checks for it. A SUT that confirms *without waiting* for the tx to
mine reads the chain too early:

  * Phase A (pending): the settlement tx is submitted but not yet mined. The SUT
    sees no event and reports UNPAID — and crucially the chain agrees no funds
    moved yet, so there is NO phantom credit and no crash.
  * Phase B (mined): the gas spike clears and the tx mines. Funds move on-chain,
    but the SUT already answered "unpaid" and never revisits — a silent loss the
    chain-truth oracle catches.

Run: pytest -m onchain tests/test_gas_spike_stuck_mempool.py
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import ANVIL_ACCOUNTS, DEFAULT_CHAIN_ID, DEFAULT_RPC, DEFAULT_TOKEN

from psv.chain import TokenView
from psv.divergence import (
    DivergenceKind,
    detect_payment_divergence,
    settlement_truth_from_balances,
)
from psv.payloads import EvmSigner, sign_authorization
from psv.reference_sut.server import ReferenceSut, SutConfig

pytestmark = pytest.mark.onchain


def _sut() -> ReferenceSut:
    # confirm_without_waiting: the vulnerable timing — check settlement immediately,
    # before the tx is guaranteed mined.
    return ReferenceSut(
        SutConfig(
            token_address=DEFAULT_TOKEN,
            merchant_address=ANVIL_ACCOUNTS["merchant"][0],
            facilitator_key=ANVIL_ACCOUNTS["deployer"][1],
            chain_id=DEFAULT_CHAIN_ID,
            rpc_endpoint=DEFAULT_RPC,
            confirm_without_waiting=True,
        )
    )


def _truth(token: TokenView, payer: str, merchant: str, nonce: str, pb: int, mb: int) -> Any:
    return settlement_truth_from_balances(
        nonce_consumed=token.authorization_used(payer, nonce),
        payer_before=pb,
        payer_after=token.balance_of(payer),
        payee_before=mb,
        payee_after=token.balance_of(merchant),
    )


def test_stuck_settlement_no_phantom_credit_then_silent_loss(
    rpc: Any, funded_token: TokenView
) -> None:
    token = funded_token
    sut = _sut()
    payer = EvmSigner.from_key(ANVIL_ACCOUNTS["payer"][1])
    merchant = ANVIL_ACCOUNTS["merchant"][0]

    quote = sut.quote()
    amount = int(quote["amount"])
    payer_before = token.balance_of(payer.address)
    merchant_before = token.balance_of(merchant)
    auth = sign_authorization(
        signer=payer,
        to=merchant,
        value=amount,
        chain_id=DEFAULT_CHAIN_ID,
        token_address=DEFAULT_TOKEN,
        token_name="USDC",
        token_version="2",
    )

    rpc.set_automine(False)  # gas spike: the settlement tx won't mine promptly
    try:
        result = sut.pay(quote["order_id"], auth.as_dict())
        # Checked before the tx mined → the SUT reports unpaid.
        assert result["settled"] is False

        # Phase A — the chain agrees nothing moved yet: consistent, no phantom credit.
        truth_pending = _truth(
            token, payer.address, merchant, auth.nonce, payer_before, merchant_before
        )
        assert not truth_pending.funds_moved
        div_pending = detect_payment_divergence(truth_pending, sut_believes_paid=result["settled"])
        assert div_pending.kind is DivergenceKind.CONSISTENT_UNPAID
        assert not div_pending.is_failure

        # The gas spike clears: the pending settlement finally mines.
        rpc.mine()
        rpc.wait_for_receipt(result["submitted_tx"])
    finally:
        rpc.set_automine(True)

    # Phase B — funds have now moved, but the SUT answered "unpaid" too early and
    # never revisits: a silent loss only the chain-truth oracle catches.
    truth_final = _truth(token, payer.address, merchant, auth.nonce, payer_before, merchant_before)
    assert truth_final.funds_moved
    div_final = detect_payment_divergence(truth_final, sut_believes_paid=result["settled"])
    assert div_final.kind is DivergenceKind.SILENT_LOSS
    assert div_final.is_failure
