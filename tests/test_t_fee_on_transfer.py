"""T-class — fee-on-transfer underpayment (Phase 3). On-chain, against Anvil.

A deceptive fee token emits a GROSS Transfer event (value = required) while only
crediting the net. The reference SUT's event-watching confirmer is fooled and
reports the order settled, but the merchant actually NETS less than required. The
harness, verifying on the real received balance delta, catches the underpayment.

Run: pytest -m onchain tests/test_t_fee_on_transfer.py
"""

from __future__ import annotations

from typing import Any

import pytest

from psv.chain import TokenView
from psv.payloads import EvmSigner, sign_authorization
from psv.reference_sut.server import ReferenceSut, SutConfig
from psv.token_quirks import net_after_fee, received_is_sufficient, underpayment

from conftest import ANVIL_ACCOUNTS, DEFAULT_CHAIN_ID, DEFAULT_RPC, DEFAULT_TOKEN, send_tx

pytestmark = pytest.mark.onchain

FEE_BPS = 200  # 2%


def test_fee_on_transfer_underpayment_caught_by_received_delta(
    rpc: Any, funded_token: TokenView
) -> None:
    token = funded_token
    deployer_key = ANVIL_ACCOUNTS["deployer"][1]
    send_tx(rpc, deployer_key, DEFAULT_TOKEN, token.set_event_mode_calldata(0), DEFAULT_CHAIN_ID)
    # Turn the token into a fee-on-transfer token (reverted by the fixture teardown).
    send_tx(rpc, deployer_key, DEFAULT_TOKEN, token.set_fee_bps_calldata(FEE_BPS), DEFAULT_CHAIN_ID)

    sut = ReferenceSut(
        SutConfig(
            token_address=DEFAULT_TOKEN, merchant_address=ANVIL_ACCOUNTS["merchant"][0],
            facilitator_key=deployer_key, chain_id=DEFAULT_CHAIN_ID, rpc_endpoint=DEFAULT_RPC,
        )
    )
    payer = EvmSigner.from_key(ANVIL_ACCOUNTS["payer"][1])
    merchant = ANVIL_ACCOUNTS["merchant"][0]

    quote = sut.quote()
    required = int(quote["amount"])
    merchant_before = token.balance_of(merchant)
    auth = sign_authorization(
        signer=payer, to=merchant, value=required, chain_id=DEFAULT_CHAIN_ID,
        token_address=DEFAULT_TOKEN, token_name="USDC", token_version="2",
    )
    result = sut.pay(quote["order_id"], auth.as_dict())

    # The SUT confirms on the GROSS Transfer event -> believes it is fully paid.
    assert result["settled"] is True

    # But the merchant only NETS required - fee.
    received = token.balance_of(merchant) - merchant_before
    assert received == net_after_fee(required, FEE_BPS)
    assert received < required

    # The harness, checking the real received delta, flags the underpayment.
    assert received_is_sufficient(received, required) is False
    assert underpayment(received, required) == required - net_after_fee(required, FEE_BPS)
