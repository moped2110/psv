"""Unit tests for the SVM settlement oracle and rail — PSV-RD-004.

The oracle reads a Solana ``getTransaction`` meta object independently of any SUT claim and
produces a ``SettlementTruth`` that the existing (chain-agnostic) divergence detectors grade:
a successful full transfer is CONSISTENT_PAID, an underpayment is UNDERPAID_CREDIT, a failed
or non-settling transaction the SUT still credits is PHANTOM_CREDIT, and an upto settlement
above the cap is OVER_AUTHORIZED_SETTLEMENT — all through the shared EVM/SVM code path.
"""

from __future__ import annotations

import pytest

from psv.divergence import (
    DivergenceKind,
    detect_metered_divergence,
    detect_payment_divergence,
)
from psv.svm_chain import (
    SOLANA_DEVNET,
    TOKEN_PROGRAM,
    SvmEvidenceError,
    SvmRailConfig,
    get_svm_rail,
    settlement_truth_from_svm_meta,
)

_MINT = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"
_PAYER = "CqgUAUcfAkZAX6TqbYU4muP6ydsg6FnSBGi7BYRAyS2V"
_PAYEE = "BR3jsXR6atrmt27e8HFWr5HfwCHUqkzxEqp6cxnngWgc"


def _bal(owner: str, amount: int) -> dict[str, object]:
    """One preTokenBalances/postTokenBalances entry for our mint."""
    return {"owner": owner, "mint": _MINT, "uiTokenAmount": {"amount": str(amount), "decimals": 6}}


def _meta(*, err: object, payer_pre: int, payer_post: int, payee_pre: int, payee_post: int) -> dict:
    """A getTransaction meta with both parties present pre and post."""
    return {
        "err": err,
        "preTokenBalances": [_bal(_PAYER, payer_pre), _bal(_PAYEE, payee_pre)],
        "postTokenBalances": [_bal(_PAYER, payer_post), _bal(_PAYEE, payee_post)],
    }


def _truth(**kw: object):
    """Build SettlementTruth from an SVM meta with our fixed mint and owners."""
    return settlement_truth_from_svm_meta(
        _meta(**kw), mint=_MINT, payer_owner=_PAYER, payee_owner=_PAYEE
    )  # type: ignore[arg-type]


def test_successful_transfer_is_consistent_paid() -> None:
    """A full on-chain transfer credited by the SUT is CONSISTENT_PAID."""
    truth = _truth(err=None, payer_pre=1000, payer_post=0, payee_pre=0, payee_post=1000)
    assert truth.funds_moved
    d = detect_payment_divergence(truth, sut_believes_paid=True, required_amount=1000)
    assert d.kind is DivergenceKind.CONSISTENT_PAID


def test_underpayment_is_flagged_through_the_shared_detector() -> None:
    """A short transfer the SUT credits as full is UNDERPAID_CREDIT."""
    truth = _truth(err=None, payer_pre=1000, payer_post=600, payee_pre=0, payee_post=400)
    d = detect_payment_divergence(truth, sut_believes_paid=True, required_amount=1000)
    assert d.kind is DivergenceKind.UNDERPAID_CREDIT
    assert d.is_failure


def test_failed_transaction_credited_is_phantom() -> None:
    """A failed tx (err set) that moved nothing but the SUT credits is PHANTOM_CREDIT."""
    truth = _truth(
        err={"InstructionError": [2, "Custom"]},
        payer_pre=1000,
        payer_post=1000,
        payee_pre=0,
        payee_post=0,
    )
    assert not truth.funds_moved
    d = detect_payment_divergence(truth, sut_believes_paid=True)
    assert d.kind is DivergenceKind.PHANTOM_CREDIT


def test_over_authorized_upto_settlement_via_metered_detector() -> None:
    """An SVM settlement above the upto cap is OVER_AUTHORIZED_SETTLEMENT."""
    truth = _truth(err=None, payer_pre=5000, payer_post=3500, payee_pre=0, payee_post=1500)
    d = detect_metered_divergence(truth, sut_believes_paid=True, authorized_max=1000)
    assert d.kind is DivergenceKind.OVER_AUTHORIZED_SETTLEMENT
    assert d.is_failure


def test_payee_ata_created_within_tx_defaults_pre_balance_to_zero() -> None:
    """A payee ATA created during the tx has no pre entry, so its pre-balance is 0."""
    meta = {
        "err": None,
        "preTokenBalances": [_bal(_PAYER, 1000)],
        "postTokenBalances": [_bal(_PAYER, 0), _bal(_PAYEE, 1000)],
    }
    truth = settlement_truth_from_svm_meta(meta, mint=_MINT, payer_owner=_PAYER, payee_owner=_PAYEE)
    assert truth.payee_delta == 1000
    assert truth.funds_moved


def test_missing_err_field_is_rejected() -> None:
    """Meta without an err field is malformed and rejected fail-closed."""
    with pytest.raises(SvmEvidenceError, match="err"):
        settlement_truth_from_svm_meta(
            {"preTokenBalances": [], "postTokenBalances": []},
            mint=_MINT,
            payer_owner=_PAYER,
            payee_owner=_PAYEE,
        )


def test_non_uint64_amount_is_rejected() -> None:
    """A negative or non-numeric token amount is rejected."""
    bad = {
        "err": None,
        "preTokenBalances": [{"owner": _PAYER, "mint": _MINT, "uiTokenAmount": {"amount": "-5"}}],
        "postTokenBalances": [],
    }
    with pytest.raises(SvmEvidenceError):
        settlement_truth_from_svm_meta(bad, mint=_MINT, payer_owner=_PAYER, payee_owner=_PAYEE)


def test_duplicate_owner_mint_account_is_ambiguous() -> None:
    """Two token accounts for the same owner+mint make the balance ambiguous."""
    meta = {
        "err": None,
        "preTokenBalances": [_bal(_PAYER, 1000), _bal(_PAYER, 5)],
        "postTokenBalances": [],
    }
    with pytest.raises(SvmEvidenceError, match="more than one"):
        settlement_truth_from_svm_meta(meta, mint=_MINT, payer_owner=_PAYER, payee_owner=_PAYEE)


def test_same_owner_for_payer_and_payee_is_rejected() -> None:
    """Payer and payee owners must differ."""
    with pytest.raises(SvmEvidenceError, match="differ"):
        settlement_truth_from_svm_meta(
            {"err": None, "preTokenBalances": [], "postTokenBalances": []},
            mint=_MINT,
            payer_owner=_PAYER,
            payee_owner=_PAYER,
        )


def test_svm_rail_identity_and_read_only_guards() -> None:
    """The known devnet rail resolves, and mint/network/signing invariants fail closed."""
    rail = get_svm_rail("usdc-solana-devnet")
    assert rail.network == SOLANA_DEVNET
    assert rail.decimals == 6
    with pytest.raises(ValueError, match="base58"):
        SvmRailConfig(
            key="x",
            label="x",
            network=SOLANA_DEVNET,
            mint="not-a-valid-mint",
            decimals=6,
            token_program=TOKEN_PROGRAM,
            attestation=rail.attestation,
        )
    with pytest.raises(ValueError, match="solana CAIP-2"):
        SvmRailConfig(
            key="x",
            label="x",
            network="eip155:1",
            mint=_MINT,
            decimals=6,
            token_program=TOKEN_PROGRAM,
            attestation=rail.attestation,
        )
    with pytest.raises(ValueError, match="signing is disabled"):
        SvmRailConfig(
            key="x",
            label="x",
            network=SOLANA_DEVNET,
            mint=_MINT,
            decimals=6,
            token_program=TOKEN_PROGRAM,
            attestation=rail.attestation,
            signing_enabled=True,
        )


def test_unknown_svm_rail_raises() -> None:
    """Requesting an unknown rail key raises KeyError."""
    with pytest.raises(KeyError):
        get_svm_rail("nope")
