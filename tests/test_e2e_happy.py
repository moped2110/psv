"""Phase 0 / H-3: one green end-to-end happy path against a real chain.

quote -> payer signs EIP-3009 -> SUT settles on-chain -> SUT confirms via the
Transfer event -> resource unlocked. Cross-checked against the independent
chain-truth oracle: the divergence detector must report CONSISTENT_PAID.

Run on a dev machine with Anvil + a deployed UpgradeableMockUSDC (see README):
    pytest -m onchain tests/test_e2e_happy.py
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import ANVIL_ACCOUNTS, DEFAULT_CHAIN_ID, DEFAULT_RPC, DEFAULT_TOKEN, send_tx

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
    config = SutConfig(
        token_address=DEFAULT_TOKEN,
        merchant_address=ANVIL_ACCOUNTS["merchant"][0],
        facilitator_key=ANVIL_ACCOUNTS["deployer"][1],
        chain_id=DEFAULT_CHAIN_ID,
        rpc_endpoint=DEFAULT_RPC,
    )
    return ReferenceSut(config)


def test_happy_path_quote_pay_confirm_deliver(rpc: Any, funded_token: TokenView) -> None:
    token = funded_token
    # Ensure the token emits the legacy Transfer event (mode 0) the SUT watches.
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
    result = sut.pay(quote["order_id"], auth.as_dict())

    # The SUT believes it settled, and the resource is unlocked.
    assert result["settled"] is True
    status = sut.status(quote["order_id"])
    assert status["paid"] is True
    assert status["resource"]

    # Independent chain truth agrees: funds moved, nonce burned, no divergence.
    truth = settlement_truth_from_balances(
        nonce_consumed=token.authorization_used(payer.address, auth.nonce),
        payer_before=payer_before,
        payer_after=token.balance_of(payer.address),
        payee_before=merchant_before,
        payee_after=token.balance_of(merchant),
    )
    assert truth.payer_delta == -amount
    assert truth.payee_delta == amount
    divergence = detect_payment_divergence(truth, sut_believes_paid=status["paid"])
    assert divergence.kind is DivergenceKind.CONSISTENT_PAID
    assert not divergence.is_failure
