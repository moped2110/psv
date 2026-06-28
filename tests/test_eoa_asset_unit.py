"""Offline tests for the EOA-as-asset guard (x402#2554 class).

An asset that is an EOA has no contract code, so an on-chain settlement against
it is a silent no-op: the tx "succeeds" but no funds move and no Transfer fires.
A robust system rejects such an asset pre-flight (``eth_getCode``); if it does
not, the harness sees the divergence (settle believed-ok, chain shows nothing
moved -> PHANTOM_CREDIT).
"""

from __future__ import annotations

from psv.chain import SettlementTruth
from psv.security_checks import asset_is_deployed_contract


def test_eoa_code_is_rejected() -> None:
    assert asset_is_deployed_contract("0x") is False          # EOA: empty code
    assert asset_is_deployed_contract("") is False
    assert asset_is_deployed_contract("0x" + "00" * 16) is False  # all-zero edge


def test_contract_code_is_accepted() -> None:
    assert asset_is_deployed_contract("0x60806040523480") is True
    assert asset_is_deployed_contract("60806040") is True     # without 0x prefix


def test_eoa_settlement_is_a_phantom_credit() -> None:
    # Model the silent no-op: a settle the SUT thinks succeeded, but chain truth
    # shows the nonce was never consumed and nothing moved.
    truth = SettlementTruth(
        nonce_consumed=False, payer_balance_after=1_000, payee_balance_after=0,
        payer_delta=0, payee_delta=0,
    )
    assert truth.funds_moved is False  # SUT-believed payment is a phantom credit
