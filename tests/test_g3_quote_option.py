"""G3 — quote as a free option. Top-3 damage scenario.

A quote locks a price against a fair value that moves. We move the fair value up
after quoting (the resource is now worth more than the locked price) and then pay:

  * a vulnerable SUT (no re-pricing) honors the stale quote and settles on-chain —
    the merchant is underpaid by the full option value, and
  * a guarded SUT (re-prices at pay time) rejects the stale quote before any
    settlement: no tx, no nonce burned, no loss.

Run: pytest -m onchain tests/test_g3_quote_option.py
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import ANVIL_ACCOUNTS, DEFAULT_CHAIN_ID, DEFAULT_RPC, DEFAULT_TOKEN, send_tx

from psv.chain import TokenView
from psv.payloads import EvmSigner, sign_authorization
from psv.quote_option import option_value
from psv.reference_sut.server import ReferenceSut, SutConfig

pytestmark = pytest.mark.onchain


def _sut(*, reprice: bool) -> ReferenceSut:
    return ReferenceSut(
        SutConfig(
            token_address=DEFAULT_TOKEN,
            merchant_address=ANVIL_ACCOUNTS["merchant"][0],
            facilitator_key=ANVIL_ACCOUNTS["deployer"][1],
            chain_id=DEFAULT_CHAIN_ID,
            rpc_endpoint=DEFAULT_RPC,
            reprice_on_pay=reprice,
            reprice_tolerance=0.02,
        )
    )


def _sign_for(sut_quote: dict[str, Any], payer: EvmSigner) -> Any:
    return sign_authorization(
        signer=payer,
        to=sut_quote["payTo"],
        value=int(sut_quote["amount"]),
        chain_id=DEFAULT_CHAIN_ID,
        token_address=DEFAULT_TOKEN,
        token_name="USDC",
        token_version="2",
    )


def test_g3_vulnerable_sut_honors_stale_quote(rpc: Any, funded_token: TokenView) -> None:
    token = funded_token
    send_tx(
        rpc,
        ANVIL_ACCOUNTS["deployer"][1],
        DEFAULT_TOKEN,
        token.set_event_mode_calldata(0),
        DEFAULT_CHAIN_ID,
    )
    payer = EvmSigner.from_key(ANVIL_ACCOUNTS["payer"][1])

    sut = _sut(reprice=False)
    quote = sut.quote()
    locked = int(quote["amount"])

    # The resource's fair value triples after the quote was locked.
    sut.fair_price = locked * 3

    auth = _sign_for(quote, payer)
    result = sut.pay(quote["order_id"], auth.as_dict())

    # Vulnerable: settles anyway, underpaying the merchant by the option value.
    assert result["settled"] is True
    assert option_value(locked, sut.fair_price) == locked * 2 > 0


def test_g3_repricing_sut_rejects_stale_quote_before_settling(
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
    payer = EvmSigner.from_key(ANVIL_ACCOUNTS["payer"][1])

    sut = _sut(reprice=True)
    quote = sut.quote()
    locked = int(quote["amount"])
    payer_before = token.balance_of(payer.address)

    sut.fair_price = locked * 3
    auth = _sign_for(quote, payer)
    result = sut.pay(quote["order_id"], auth.as_dict())

    # Guarded: rejected before any on-chain action.
    assert result["settled"] is False
    assert result["reason"] == "stale_quote"
    assert result.get("submitted_tx") is None
    assert token.balance_of(payer.address) == payer_before  # no funds moved
    assert token.authorization_used(payer.address, auth.nonce) is False  # nonce intact
