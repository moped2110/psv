"""I-class — idempotency of settlement (Phase 2). On-chain, against Anvil.

Paying the same order twice (a retry, a double-click, a redelivered webhook)
must not settle twice. We show:
  * a vulnerable SUT re-submits a second on-chain settlement, and
  * an idempotent SUT short-circuits the retry with the cached result.

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
from psv.payloads import EvmSigner, sign_authorization
from psv.reference_sut.server import ReferenceSut, SutConfig

pytestmark = pytest.mark.onchain


def _sut(*, idempotent: bool) -> ReferenceSut:
    return ReferenceSut(
        SutConfig(
            token_address=DEFAULT_TOKEN,
            merchant_address=ANVIL_ACCOUNTS["merchant"][0],
            facilitator_key=ANVIL_ACCOUNTS["deployer"][1],
            chain_id=DEFAULT_CHAIN_ID,
            rpc_endpoint=DEFAULT_RPC,
            idempotent_pay=idempotent,
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
