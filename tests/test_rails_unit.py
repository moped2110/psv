"""Atomic, attributable and rail-attested chain-truth tests."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from reconcile_fakes import (
    NONCE,
    PAYEE,
    PAYER,
    TX_HASH,
    strict_token,
    transfer_log,
)

from psv.anvil import RpcClient
from psv.divergence import DivergenceKind
from psv.rails import (
    KNOWN_RAILS,
    ChainEvidenceError,
    check_rail_drift,
    get_rail,
    reconcile_live,
)


def _reconcile(**overrides: object):  # type: ignore[no-untyped-def]
    rail = get_rail("mock-anvil")
    token_options = dict(overrides.pop("token_options", {}))
    arguments = {
        "payer": PAYER,
        "payee": PAYEE,
        "nonce": NONCE,
        "transaction_hash": TX_HASH,
        "log_index": 0,
        "required_amount": 100,
        "payer_before": 1000,
        "payee_before": 0,
        "sut_believes_paid": True,
    }
    arguments.update(overrides)
    return reconcile_live(strict_token(rail, **token_options), rail, **arguments)


def test_known_rails_have_reviewed_runtime_metadata() -> None:
    assert set(KNOWN_RAILS) >= {"mock-anvil", "usdc-base", "jpyc-polygon", "eurc-base"}
    for rail in KNOWN_RAILS.values():
        assert rail.attestation.authoritative_sources
        assert rail.attestation.reviewed_on.isoformat() == "2026-07-18"
        assert rail.attestation.interface == "eip3009"
        assert rail.attestation.expected_decimals == rail.decimals
        assert rail.signing_enabled is False
        assert rail.finality.minimum_confirmations >= 1
    assert get_rail("mock-anvil").attestation.calibrated is True
    assert get_rail("usdc-base").attestation.calibrated is True
    assert get_rail("eurc-base").attestation.calibrated is True
    assert get_rail("jpyc-polygon").attestation.calibrated is False


def test_eurc_is_the_reviewed_read_only_eur_rail() -> None:
    eurc = get_rail("eurc-base")
    assert eurc.token_address == "0x60a3e35cc302bfa44cb288bc5a4f316fdb1adb42"
    assert eurc.chain_id == 8453 and eurc.decimals == 6
    assert eurc.token_name is None and eurc.token_version is None
    assert eurc.finality.block_tag == "finalized"
    assert eurc.attestation.reviewed_block_number == 48_783_151
    assert eurc.attestation.expected_code_sha256 == (
        "c9cf7c3f11c4d3d818801b5a965cea3bae6ff3b9b923242b91a9b4e5888e7835"
    )
    assert eurc.attestation.implementation_address == ("0x2ce6311ddae708829bc0784c967b7d77d19fd779")


def test_unknown_rail_raises() -> None:
    with pytest.raises(KeyError, match="nope"):
        get_rail("nope")


def test_consistent_paid_has_exact_pinned_evidence() -> None:
    result = _reconcile()
    assert result.kind is DivergenceKind.CONSISTENT_PAID
    assert result.evidence.transaction_hash == TX_HASH
    assert result.evidence.log_index == 0
    assert result.evidence.authorization_log_index == 1
    assert result.evidence.settlement_block_number == 10
    assert result.evidence.finality_block_number == 12
    assert result.evidence.received_amount == 100


def test_underpaid_credit_uses_actual_pinned_balance_delta() -> None:
    result = _reconcile(token_options={"payer_after": 900, "payee_after": 90, "event_value": 100})
    assert result.kind is DivergenceKind.UNDERPAID_CREDIT
    assert result.is_failure and result.evidence.received_amount == 90


def test_exact_and_overpayment_are_not_underpaid() -> None:
    exact = _reconcile()
    assert exact.kind is DivergenceKind.CONSISTENT_PAID
    over = _reconcile(
        token_options={"payer_after": 800, "payee_after": 200, "event_value": 200},
        required_amount=100,
    )
    assert over.kind is DivergenceKind.CONSISTENT_PAID


@pytest.mark.parametrize("required", [0, -1, 2**256])
def test_required_amount_must_be_positive_uint256(required: int) -> None:
    with pytest.raises(ValueError, match="required_amount"):
        _reconcile(required_amount=required)


def test_uint256_max_required_amount_is_supported() -> None:
    maximum = 2**256 - 1
    result = _reconcile(
        token_options={
            "payer_before": maximum,
            "payer_after": 0,
            "payee_after": maximum,
            "event_value": maximum,
        },
        required_amount=maximum,
        payer_before=maximum,
    )
    assert result.kind is DivergenceKind.CONSISTENT_PAID


def test_rpc_chain_mismatch_fails_before_token_reads() -> None:
    with pytest.raises(ChainEvidenceError, match="chain mismatch"):
        _reconcile(token_options={"live_chain_id": 1})


def test_missing_or_mismatched_code_fails_closed() -> None:
    with pytest.raises(ChainEvidenceError, match="no deployed runtime bytecode"):
        _reconcile(token_options={"code": "0x"})
    rail = get_rail("mock-anvil")
    mismatched = replace(
        rail,
        attestation=replace(rail.attestation, expected_code_sha256="00" * 32),
    )
    with pytest.raises(ChainEvidenceError, match="runtime code"):
        reconcile_live(
            strict_token(mismatched),
            mismatched,
            payer=PAYER,
            payee=PAYEE,
            nonce=NONCE,
            transaction_hash=TX_HASH,
            log_index=0,
            required_amount=100,
            payer_before=1000,
            payee_before=0,
            sut_believes_paid=True,
        )


def test_uncalibrated_live_rail_fails_before_token_reads() -> None:
    rail = get_rail("jpyc-polygon")
    with pytest.raises(ChainEvidenceError, match="uncalibrated"):
        reconcile_live(
            strict_token(rail),
            rail,
            payer=PAYER,
            payee=PAYEE,
            nonce=NONCE,
            transaction_hash=TX_HASH,
            log_index=0,
            required_amount=100,
            payer_before=1000,
            payee_before=0,
            sut_believes_paid=True,
        )


def test_read_only_drift_check_exports_pinned_runtime_observation() -> None:
    rail = get_rail("mock-anvil")
    check = check_rail_drift(rail, strict_token(rail).rpc)
    assert check.matches and check.calibrated
    assert check.block_number == 12 and len(check.code_sha256) == 64
    assert check.as_dict()["readOnly"] is True


def test_proxy_implementation_drift_fails_closed() -> None:
    rail = get_rail("usdc-base")

    def transport(request: dict[str, Any]) -> dict[str, Any]:
        method = request["method"]
        if method == "eth_chainId":
            result: object = hex(rail.chain_id)
        elif method == "eth_getBlockByNumber":
            result = {
                "number": hex(48_783_151),
                "timestamp": "0x1",
                "hash": rail.attestation.reviewed_block_hash,
                "parentHash": "0x" + "01" * 32,
                "transactions": [],
            }
        elif method == "eth_getCode":
            result = "0x6000"
        elif method == "eth_getStorageAt":
            result = "0x" + "00" * 12 + "99" * 20
        else:
            raise AssertionError(method)
        return {"jsonrpc": "2.0", "id": request["id"], "result": result}

    with pytest.raises(ChainEvidenceError, match="implementation drift"):
        check_rail_drift(rail, RpcClient(transport=transport))


def test_unrelated_same_block_inbound_transfer_is_inconclusive() -> None:
    rail = get_rail("mock-anvil")
    unrelated = transfer_log(
        token=rail.token_address,
        value=1,
        tx_hash="0x" + "ee" * 32,
        log_index=3,
        payer="0x" + "33" * 20,
    )
    with pytest.raises(ChainEvidenceError, match="race"):
        _reconcile(token_options={"extra_same_block_log": unrelated})


def test_removed_log_and_reorg_during_reads_are_inconclusive() -> None:
    with pytest.raises(ChainEvidenceError, match="removed"):
        _reconcile(token_options={"removed": True})
    with pytest.raises(ChainEvidenceError, match="reorged or replaced"):
        _reconcile(token_options={"reorg_on_recheck": True})


def test_reverted_tx_with_nonce_consumed_elsewhere_is_inconclusive() -> None:
    with pytest.raises(ChainEvidenceError, match="another settlement"):
        _reconcile(
            token_options={
                "receipt_status": 0,
                "nonce_used": True,
                "payer_after": 1000,
                "payee_after": 0,
            }
        )


def test_caller_before_snapshot_must_match_parent_block() -> None:
    with pytest.raises(ChainEvidenceError, match="before-balances"):
        _reconcile(payer_before=999)
