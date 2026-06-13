"""D3 — backup/restore vs. chain divergence. Top-3 damage scenario.

A payment settles on-chain and is booked in the SUT's ledger. The ledger is then
rolled back to an earlier backup (crash + restore), losing that booking while the
chain keeps the payment. We show:

  * without reconciliation the SUT silently forgets a real payment — the harness's
    chain-truth oracle catches the resulting SILENT_LOSS, and
  * a reconciliation job surfaces exactly the forgotten on-chain credit and (when
    enabled) heals the ledger.

Run: pytest -m onchain tests/test_d3_reconciliation.py
"""

from __future__ import annotations

from typing import Any

import pytest

from psv.chain import TokenView
from psv.divergence import (
    DivergenceKind,
    detect_payment_divergence,
    settlement_truth_from_balances,
)
from psv.payloads import Authorization, EvmSigner, sign_authorization
from psv.reference_sut.server import ReferenceSut, SutConfig

from conftest import ANVIL_ACCOUNTS, DEFAULT_CHAIN_ID, DEFAULT_RPC, DEFAULT_TOKEN, send_tx

pytestmark = pytest.mark.onchain


def _sut(*, reconciliation: bool) -> ReferenceSut:
    return ReferenceSut(
        SutConfig(
            token_address=DEFAULT_TOKEN,
            merchant_address=ANVIL_ACCOUNTS["merchant"][0],
            facilitator_key=ANVIL_ACCOUNTS["deployer"][1],
            chain_id=DEFAULT_CHAIN_ID,
            rpc_endpoint=DEFAULT_RPC,
            reconciliation_enabled=reconciliation,
        )
    )


def _pay(sut: ReferenceSut, token: TokenView, payer: EvmSigner, merchant: str) -> tuple[str, Authorization, Any]:
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
    assert result["settled"] is True
    truth = settlement_truth_from_balances(
        nonce_consumed=token.authorization_used(payer.address, auth.nonce),
        payer_before=payer_before, payer_after=token.balance_of(payer.address),
        payee_before=merchant_before, payee_after=token.balance_of(merchant),
    )
    return quote["order_id"], auth, truth


def test_d3_restore_causes_silent_loss_caught_by_reconciliation(
    rpc: Any, funded_token: TokenView
) -> None:
    token = funded_token
    send_tx(rpc, ANVIL_ACCOUNTS["deployer"][1], DEFAULT_TOKEN,
            token.set_event_mode_calldata(0), DEFAULT_CHAIN_ID)
    payer = EvmSigner.from_key(ANVIL_ACCOUNTS["payer"][1])
    merchant = ANVIL_ACCOUNTS["merchant"][0]

    sut = _sut(reconciliation=False)  # the vulnerable system

    # Payment A settles and is booked.
    _id_a, _auth_a, _truth_a = _pay(sut, token, payer, merchant)
    backup = sut.backup_ledger()  # a backup taken right after A

    # Payment B settles on-chain and is booked AFTER the backup.
    id_b, _auth_b, truth_b = _pay(sut, token, payer, merchant)
    assert sut.status(id_b)["paid"] is True
    assert truth_b.funds_moved

    # Crash + restore to the older backup: B is forgotten by the ledger.
    sut.restore_ledger(backup)
    status_b = sut.status(id_b)
    assert status_b["known"] is False  # the order is simply gone
    # ...yet the money really moved. The oracle exposes the silent loss.
    divergence = detect_payment_divergence(truth_b, sut_believes_paid=status_b["paid"])
    assert divergence.kind is DivergenceKind.SILENT_LOSS
    assert divergence.is_failure

    # A reconciliation pass surfaces exactly the forgotten on-chain credit.
    gap = sut.reconcile(from_block=0)
    assert sut.orders.get(id_b) is None  # vulnerable SUT doesn't heal
    assert len(gap) == 1
    assert gap[0].payer.lower() == payer.address.lower()

    # Turn reconciliation on: the job now heals the ledger and closes the gap.
    healing = _sut(reconciliation=True)
    healing.restore_ledger(backup)
    healed = healing.reconcile(from_block=0)
    assert len(healed) == 1
    recovered = [o for o in healing.orders.values() if o.recovered]
    assert recovered and recovered[0].paid
    assert recovered[0].submitted_tx == healed[0].tx_hash
    # A second pass finds nothing left to reconcile.
    assert healing.reconcile(from_block=0) == []
