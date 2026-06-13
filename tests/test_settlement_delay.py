"""Settlement delay — confirming before the tx is mined (Phase 2). On-chain.

A SUT that checks for the settlement event *immediately* after submitting, before
the tx is mined, concludes "unpaid" — a false negative. We make the delay
deterministic with Anvil's automine switch: submit while mining is paused, observe
the premature "unpaid", then mine and show the payment really went through. The
SUT still believes unpaid → the harness flags a SILENT_LOSS.

Run: pytest -m onchain tests/test_settlement_delay.py
"""

from __future__ import annotations

from typing import Any

import pytest

from psv.chain import TokenView
from psv.divergence import DivergenceKind, detect_payment_divergence, settlement_truth_from_balances
from psv.payloads import EvmSigner, sign_authorization
from psv.reference_sut.server import ReferenceSut, SutConfig

from conftest import ANVIL_ACCOUNTS, DEFAULT_CHAIN_ID, DEFAULT_RPC, DEFAULT_TOKEN, send_tx

pytestmark = pytest.mark.onchain


def test_premature_confirmation_is_a_false_negative_caught_by_harness(
    rpc: Any, funded_token: TokenView
) -> None:
    token = funded_token
    send_tx(rpc, ANVIL_ACCOUNTS["deployer"][1], DEFAULT_TOKEN,
            token.set_event_mode_calldata(0), DEFAULT_CHAIN_ID)
    sut = ReferenceSut(
        SutConfig(
            token_address=DEFAULT_TOKEN, merchant_address=ANVIL_ACCOUNTS["merchant"][0],
            facilitator_key=ANVIL_ACCOUNTS["deployer"][1], chain_id=DEFAULT_CHAIN_ID,
            rpc_endpoint=DEFAULT_RPC, confirm_without_waiting=True,  # the vulnerable mode
        )
    )
    payer = EvmSigner.from_key(ANVIL_ACCOUNTS["payer"][1])
    merchant = ANVIL_ACCOUNTS["merchant"][0]

    quote = sut.quote()
    amount = int(quote["amount"])
    payer_before = token.balance_of(payer.address)
    merchant_before = token.balance_of(merchant)
    auth = sign_authorization(
        signer=payer, to=merchant, value=amount, chain_id=DEFAULT_CHAIN_ID,
        token_address=DEFAULT_TOKEN, token_name="USDC", token_version="2",
    )

    rpc.set_automine(False)  # pause mining: settlements now queue, not mine
    try:
        result = sut.pay(quote["order_id"], auth.as_dict())
        # Checked too early: the tx is queued but not mined -> looks unpaid.
        assert result["settled"] is False
        assert sut.status(quote["order_id"])["paid"] is False
        assert token.balance_of(merchant) == merchant_before  # nothing settled yet

        rpc.mine(1)  # the queued settlement is now mined
    finally:
        rpc.set_automine(True)

    # The payment really went through.
    payer_after = token.balance_of(payer.address)
    merchant_after = token.balance_of(merchant)
    assert merchant_after == merchant_before + amount
    truth = settlement_truth_from_balances(
        nonce_consumed=token.authorization_used(payer.address, auth.nonce),
        payer_before=payer_before, payer_after=payer_after,
        payee_before=merchant_before, payee_after=merchant_after,
    )
    assert truth.funds_moved

    # ...but the SUT concluded unpaid -> the harness flags a silent loss.
    divergence = detect_payment_divergence(
        truth, sut_believes_paid=sut.status(quote["order_id"])["paid"]
    )
    assert divergence.kind is DivergenceKind.SILENT_LOSS
    assert divergence.is_failure
