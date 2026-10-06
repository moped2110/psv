"""I-class — idempotency of settlement (Phase 2). On-chain, against Anvil.

Paying the same order twice (a retry, a double-click, a redelivered webhook)
must not settle twice. We show:
  * a vulnerable SUT re-submits a second on-chain settlement, and
  * an idempotent SUT short-circuits the retry with the cached result.

PSV-I-002 covers the retry after x402's non-terminal ``settlement_pending``: the
settlement was broadcast but its receipt could not be fetched (automine off), so the
SUT answers pending with the tx hash. The resource server's single retry must wait on
that hash, not broadcast the authorization a second time. A SUT that does broadcast
again lands a second, reverting transaction and books the order as unpaid even though
the first one paid: a silent loss.

In both cases the faithful EIP-3009 token credits the merchant exactly once (the
on-chain nonce guard blocks the replayed authorization) — so the harness's signal
is the redundant *submission* (wasted gas, and a double-credit in any system that
books credits without that on-chain protection).

Run: pytest -m onchain tests/test_i_idempotency.py
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import ANVIL_ACCOUNTS, DEFAULT_CHAIN_ID, DEFAULT_RPC, DEFAULT_TOKEN, send_tx

from psv.chain import TokenView
from psv.divergence import DivergenceKind, detect_payment_divergence, settlement_truth_from_balances
from psv.payloads import EvmSigner, sign_authorization
from psv.reference_sut.server import ReferenceSut, SutConfig
from psv.sut import PayOutcome, parse_pay

pytestmark = pytest.mark.onchain


def _sut(*, idempotent: bool, receipt_tries: int = 50) -> ReferenceSut:
    return ReferenceSut(
        SutConfig(
            token_address=DEFAULT_TOKEN,
            merchant_address=ANVIL_ACCOUNTS["merchant"][0],
            facilitator_key=ANVIL_ACCOUNTS["deployer"][1],
            chain_id=DEFAULT_CHAIN_ID,
            rpc_endpoint=DEFAULT_RPC,
            idempotent_pay=idempotent,
            receipt_tries=receipt_tries,
        )
    )


def _pay_twice(sut: ReferenceSut, token: TokenView) -> tuple[str, int, int]:
    payer = EvmSigner.from_key(ANVIL_ACCOUNTS["payer"][1])
    merchant = ANVIL_ACCOUNTS["merchant"][0]
    quote = sut.quote()
    amount = int(quote["amount"])
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
    sut.pay(quote["order_id"], auth.as_dict())
    sut.pay(quote["order_id"], auth.as_dict())  # retry with the same order + auth
    credited = token.balance_of(merchant) - merchant_before
    return quote["order_id"], amount, credited


def test_idempotent_sut_does_not_resubmit(rpc: Any, funded_token: TokenView) -> None:
    token = funded_token
    send_tx(
        rpc,
        ANVIL_ACCOUNTS["deployer"][1],
        DEFAULT_TOKEN,
        token.set_event_mode_calldata(0),
        DEFAULT_CHAIN_ID,
    )
    sut = _sut(idempotent=True)
    oid, amount, credited = _pay_twice(sut, token)
    assert credited == amount  # merchant credited exactly once
    assert sut.orders[oid].settle_attempts == 1  # retry was short-circuited


def test_vulnerable_sut_resubmits_second_settlement(rpc: Any, funded_token: TokenView) -> None:
    token = funded_token
    send_tx(
        rpc,
        ANVIL_ACCOUNTS["deployer"][1],
        DEFAULT_TOKEN,
        token.set_event_mode_calldata(0),
        DEFAULT_CHAIN_ID,
    )
    sut = _sut(idempotent=False)
    oid, amount, credited = _pay_twice(sut, token)
    assert credited == amount  # token's nonce guard still prevents a double-debit
    assert sut.orders[oid].settle_attempts == 2  # but the SUT redundantly re-submitted


def _facilitator_tx_count(rpc: Any) -> int:
    return int(rpc.call("eth_getTransactionCount", [ANVIL_ACCOUNTS["deployer"][0], "latest"]), 16)


def _pending_then_retry(
    rpc: Any, token: TokenView, *, idempotent: bool
) -> tuple[ReferenceSut, str, Any, Any, int, int]:
    """Pay once with automine off (settlement_pending), mine, then retry once."""
    send_tx(
        rpc,
        ANVIL_ACCOUNTS["deployer"][1],
        DEFAULT_TOKEN,
        token.set_event_mode_calldata(0),
        DEFAULT_CHAIN_ID,
    )
    sut = _sut(idempotent=idempotent, receipt_tries=3)
    payer = EvmSigner.from_key(ANVIL_ACCOUNTS["payer"][1])
    merchant = ANVIL_ACCOUNTS["merchant"][0]
    quote = sut.quote()
    oid = quote["order_id"]
    amount = int(quote["amount"])
    payer_before = token.balance_of(payer.address)
    merchant_before = token.balance_of(merchant)
    txs_before = _facilitator_tx_count(rpc)
    auth = sign_authorization(
        signer=payer,
        to=merchant,
        value=amount,
        chain_id=DEFAULT_CHAIN_ID,
        token_address=DEFAULT_TOKEN,
        token_name="USDC",
        token_version="2",
    )
    rpc.set_automine(False)  # the receipt cannot be fetched: confirmation not established
    try:
        first = parse_pay(sut.pay(oid, auth.as_dict()), expected_order_id=oid)
        assert first.outcome is PayOutcome.PENDING
        assert first.submitted_tx is not None
        rpc.mine()  # the pending settlement lands
        rpc.wait_for_receipt(first.submitted_tx)
    finally:
        rpc.set_automine(True)
    retry = parse_pay(sut.pay(oid, auth.as_dict()), expected_order_id=oid)  # the single retry
    truth = settlement_truth_from_balances(
        nonce_consumed=token.authorization_used(payer.address, auth.nonce),
        payer_before=payer_before,
        payer_after=token.balance_of(payer.address),
        payee_before=merchant_before,
        payee_after=token.balance_of(merchant),
    )
    broadcasts = _facilitator_tx_count(rpc) - txs_before
    return sut, oid, first, retry, broadcasts, truth.payee_delta


def test_retry_after_settlement_pending_does_not_broadcast_again(
    rpc: Any, funded_token: TokenView
) -> None:
    sut, oid, first, retry, broadcasts, credited = _pending_then_retry(
        rpc, funded_token, idempotent=True
    )
    assert retry.outcome is PayOutcome.SETTLED
    assert retry.submitted_tx == first.submitted_tx  # waited on the pending hash
    assert broadcasts == 1  # one settlement transaction on chain, no second broadcast
    assert sut.orders[oid].settle_attempts == 1
    assert credited == int(sut.orders[oid].amount)
    assert sut.status(oid)["paid"] is True


def test_vulnerable_retry_after_settlement_pending_broadcasts_again_and_loses_the_payment(
    rpc: Any, funded_token: TokenView
) -> None:
    token = funded_token
    payer = EvmSigner.from_key(ANVIL_ACCOUNTS["payer"][1]).address
    merchant = ANVIL_ACCOUNTS["merchant"][0]
    payer_before = token.balance_of(payer)
    merchant_before = token.balance_of(merchant)
    sut, oid, first, retry, broadcasts, credited = _pending_then_retry(rpc, token, idempotent=False)
    assert broadcasts == 2  # the retry broadcast the consumed authorization again
    assert sut.orders[oid].settle_attempts == 2
    assert retry.submitted_tx != first.submitted_tx
    assert credited == int(sut.orders[oid].amount)  # the nonce guard: debited once
    # The second transaction reverts, the SUT confirms against it and books the order
    # unpaid although the first settlement paid: the chain-truth oracle sees the loss.
    assert retry.outcome is PayOutcome.UNSETTLED
    truth = settlement_truth_from_balances(
        nonce_consumed=True,
        payer_before=payer_before,
        payer_after=token.balance_of(payer),
        payee_before=merchant_before,
        payee_after=token.balance_of(merchant),
    )
    div = detect_payment_divergence(truth, sut_believes_paid=sut.status(oid)["paid"])
    assert div.kind is DivergenceKind.SILENT_LOSS
