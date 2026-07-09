"""Facilitator crash mid-settlement — an orphaned settlement. On-chain, Anvil.

A payment system settles in two steps: submit the on-chain settlement, then
record it in the ledger. If the facilitator/process dies *between* those steps —
the tx is mined, but the system never books it — the money moves and the order
stays "unpaid" forever. We simulate the crash by settling the authorization
on-chain out-of-band (as the facilitator would) and never letting the SUT record
it. The chain-truth oracle then catches the orphaned settlement as a critical
SILENT_LOSS — the exact gap a black-box tester cannot see.

Run: pytest -m onchain tests/test_facilitator_crash.py
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import ANVIL_ACCOUNTS, DEFAULT_CHAIN_ID, DEFAULT_RPC, DEFAULT_TOKEN, send_tx

from psv.chain import TokenView
from psv.divergence import (
    DivergenceKind,
    Severity,
    detect_payment_divergence,
    settlement_truth_from_balances,
)
from psv.payloads import EvmSigner, sign_authorization
from psv.reference_sut.server import ReferenceSut, SutConfig

pytestmark = pytest.mark.onchain


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


def test_facilitator_crash_after_settle_is_caught_as_silent_loss(
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

    # The facilitator settles on-chain, then "crashes" before the SUT books it:
    # settle the authorization directly and never drive sut.pay() to completion.
    send_tx(
        rpc,
        ANVIL_ACCOUNTS["deployer"][1],
        DEFAULT_TOKEN,
        token.settle_calldata(
            from_addr=payer.address,
            to=merchant,
            value=amount,
            valid_after=auth.valid_after,
            valid_before=auth.valid_before,
            nonce=auth.nonce,
            signature=auth.signature,
        ),
        DEFAULT_CHAIN_ID,
    )

    # Chain truth: the payment really happened.
    truth = settlement_truth_from_balances(
        nonce_consumed=token.authorization_used(payer.address, auth.nonce),
        payer_before=payer_before,
        payer_after=token.balance_of(payer.address),
        payee_before=merchant_before,
        payee_after=token.balance_of(merchant),
    )
    assert truth.funds_moved
    assert token.authorization_used(payer.address, auth.nonce)

    # The SUT never recorded the settlement (the facilitator died mid-flight).
    status = sut.status(quote["order_id"])
    assert status["paid"] is False

    # The harness catches the orphaned settlement: a critical silent loss.
    div = detect_payment_divergence(truth, sut_believes_paid=status["paid"])
    assert div.kind is DivergenceKind.SILENT_LOSS
    assert div.severity is Severity.CRITICAL
    assert div.is_failure
