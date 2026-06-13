"""SC1 — Contract-Upgrade / ABI (event) drift. Top-3 damage scenario.

The reference SUT confirms settlement by watching the legacy ``Transfer`` event.
We flip the token's settlement event signature IN PLACE (same address, same
balances) — exactly what a proxy upgrade would do — and show the consequence:

  * Phase A (baseline, mode 0): a payment settles and the SUT registers it.
    Proves the confirmer actually works, so Phase B is a real regression.
  * Phase B (drift, mode 1): a payment moves funds on-chain identically, but the
    SUT's event filter matches nothing and reports the order UNPAID.

The point of the harness is the last assertion: the independent chain-truth
oracle sees the money move, the SUT does not, and the divergence detector raises
a CRITICAL **SILENT_LOSS**. A black-box conformance tester cannot see this —
only a system-level oracle that reads the chain directly can.

Run: pytest -m onchain tests/test_sc1_abi_drift.py
"""

from __future__ import annotations

from typing import Any

import pytest

from psv.chain import TokenView
from psv.divergence import (
    DivergenceKind,
    Severity,
    detect_payment_divergence,
    settlement_truth_from_balances,
)
from psv.payloads import Authorization, EvmSigner, sign_authorization
from psv.reference_sut.server import ReferenceSut, SutConfig

from conftest import ANVIL_ACCOUNTS, DEFAULT_CHAIN_ID, DEFAULT_RPC, DEFAULT_TOKEN, send_tx

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


def _set_mode(rpc: Any, token: TokenView, mode: int) -> None:
    send_tx(rpc, ANVIL_ACCOUNTS["deployer"][1], DEFAULT_TOKEN,
            token.set_event_mode_calldata(mode), DEFAULT_CHAIN_ID)
    assert token.event_mode() == mode


def _pay_once(sut: ReferenceSut, token: TokenView, payer: EvmSigner, merchant: str) -> tuple[Any, Authorization, Any]:
    quote = sut.quote()
    amount = int(quote["amount"])
    payer_before = token.balance_of(payer.address)
    merchant_before = token.balance_of(merchant)
    auth = sign_authorization(
        signer=payer, to=merchant, value=amount,
        chain_id=DEFAULT_CHAIN_ID, token_address=DEFAULT_TOKEN,
        token_name="USDC", token_version="2",
    )
    result = sut.pay(quote["order_id"], auth.as_dict())
    truth = settlement_truth_from_balances(
        nonce_consumed=token.authorization_used(payer.address, auth.nonce),
        payer_before=payer_before, payer_after=token.balance_of(payer.address),
        payee_before=merchant_before, payee_after=token.balance_of(merchant),
    )
    return result, auth, truth


def test_sc1_event_drift_causes_silent_loss_detected_by_harness(
    rpc: Any, funded_token: TokenView
) -> None:
    token = funded_token
    sut = _sut()
    payer = EvmSigner.from_key(ANVIL_ACCOUNTS["payer"][1])
    merchant = ANVIL_ACCOUNTS["merchant"][0]

    # --- Phase A: baseline, legacy event. The SUT must work correctly. --------
    _set_mode(rpc, token, 0)
    result_a, _auth_a, truth_a = _pay_once(sut, token, payer, merchant)
    assert result_a["settled"] is True
    assert truth_a.funds_moved
    div_a = detect_payment_divergence(truth_a, sut_believes_paid=result_a["settled"])
    assert div_a.kind is DivergenceKind.CONSISTENT_PAID

    # --- Phase B: the drift. Event signature changes in place. ----------------
    _set_mode(rpc, token, 1)
    result_b, auth_b, truth_b = _pay_once(sut, token, payer, merchant)

    # On-chain reality: the payment really happened.
    assert truth_b.funds_moved, "funds must move on-chain even after the drift"
    assert token.authorization_used(payer.address, auth_b.nonce)

    # The SUT, watching the old event, is blind: it believes the order is unpaid.
    assert result_b["settled"] is False, "SUT should fail to confirm the drifted event"

    # The harness catches what the SUT cannot: a critical SILENT LOSS.
    divergence = detect_payment_divergence(truth_b, sut_believes_paid=result_b["settled"])
    assert divergence.kind is DivergenceKind.SILENT_LOSS
    assert divergence.severity is Severity.CRITICAL
    assert divergence.is_failure
