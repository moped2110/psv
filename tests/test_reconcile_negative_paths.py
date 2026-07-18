"""Fail-closed branch coverage for reconciliation, reports, rails, and CLI."""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from reconcile_fakes import BLOCK_HASH, NONCE, PAYEE, PAYER, TX_HASH, strict_token

from psv import cli
from psv.divergence import DivergenceKind, Severity
from psv.rails import (
    ChainEvidenceError,
    FinalityPolicy,
    RailDriftCheck,
    _code_fingerprint,
    _exact_hash,
    _implementation_identity,
    _quantity,
    _verify_review_anchor,
    check_rail_drift,
    get_rail,
    reconcile_live,
)
from psv.reconciliation import (
    OnChainCredit,
    ReconciliationError,
    SettlementIdentity,
    _addr_from_topic,
    _exact_hex,
    decode_transfer_log,
)
from psv.report import ReconReport, validate_report_document


@pytest.mark.parametrize(("tag", "confirmations"), [("pending", 1), ("safe", 0), ("safe", True)])
def test_finality_policy_rejects_invalid_domain(tag: str, confirmations: int) -> None:
    with pytest.raises(ValueError):
        FinalityPolicy(tag, confirmations)


@pytest.mark.parametrize(
    "changes",
    [
        {"version": ""},
        {"authoritative_sources": ()},
        {"interface": "erc20"},
        {"network_class": "unknown"},
        {"expected_decimals": 37},
        {"reviewed_block_number": 1, "reviewed_block_hash": None},
        {"reviewed_block_number": -1, "reviewed_block_hash": BLOCK_HASH},
        {"reviewed_block_number": 1, "reviewed_block_hash": "0x1"},
        {"expected_code_sha256": "ABC"},
        {"implementation_address": "0x1"},
        {"proxy_implementation_slot": "0x1"},
        {"implementation_code_sha256": "xyz"},
        {"network_class": "mainnet", "calibrated": True},
    ],
)
def test_rail_attestation_rejects_incomplete_or_malformed_metadata(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        replace(get_rail("mock-anvil").attestation, **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"chain_id": 0},
        {"token_address": "0x1"},
        {"decimals": -1},
        {"signing_enabled": True},
        {"decimals": 7},
        {"token_name": "wrong-domain"},
    ],
)
def test_rail_config_rejects_unsafe_or_inconsistent_metadata(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        replace(get_rail("mock-anvil"), **changes)


def test_live_reconciliation_forwarding_properties_are_stable() -> None:
    rail = get_rail("mock-anvil")
    result = reconcile_live(
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
    assert result.kind is DivergenceKind.CONSISTENT_PAID
    assert result.severity is Severity.OK
    assert "Funds moved" in result.message
    assert result.is_failure is False


def test_rail_scalar_decoders_and_code_fingerprint_fail_closed() -> None:
    with pytest.raises(ChainEvidenceError, match="quantity"):
        _quantity("123", "quantity")
    with pytest.raises(ChainEvidenceError, match="hash"):
        _exact_hash("0x1", "hash")
    with pytest.raises(ChainEvidenceError, match="malformed"):
        _code_fingerprint("0xzz")


class _ProxyRpc:
    def __init__(self, word: object, code: str = "0x6000") -> None:
        self.word = word
        self.code = code

    def call(self, method: str, params: list[object]) -> object:
        assert method == "eth_getStorageAt" and params
        return self.word

    def get_code(self, address: str, block: int) -> str:
        return self.code


@pytest.mark.parametrize("word", [None, "0x1", "0x" + "11" * 32])
def test_proxy_slot_shape_is_strict(word: object) -> None:
    rail = get_rail("usdc-base")
    with pytest.raises(ChainEvidenceError):
        _implementation_identity(rail, _ProxyRpc(word), 1)  # type: ignore[arg-type]


def test_proxy_implementation_code_hash_mismatch_is_rejected() -> None:
    rail = get_rail("usdc-base")
    address = rail.attestation.implementation_address
    assert address is not None
    word = "0x" + "00" * 12 + address[2:]
    with pytest.raises(ChainEvidenceError, match="runtime code"):
        _implementation_identity(rail, _ProxyRpc(word), 1)  # type: ignore[arg-type]


def test_review_anchor_mismatch_is_rejected() -> None:
    rail = get_rail("usdc-base")
    rpc = SimpleNamespace(get_block=lambda block: {"number": hex(block), "hash": "0x" + "ff" * 32})
    with pytest.raises(ChainEvidenceError, match="not canonical"):
        _verify_review_anchor(rail, rpc)  # type: ignore[arg-type]


def test_drift_check_reports_uncalibrated_and_code_mismatch() -> None:
    local = get_rail("mock-anvil")
    uncalibrated_rail = replace(
        local,
        attestation=replace(local.attestation, network_class="testnet", calibrated=False),
    )
    uncalibrated = check_rail_drift(uncalibrated_rail, strict_token(uncalibrated_rail).rpc)
    assert not uncalibrated.matches and "uncalibrated" in uncalibrated.reason

    testnet_attestation = replace(
        local.attestation,
        network_class="testnet",
        expected_code_sha256="00" * 32,
    )
    testnet = replace(local, attestation=testnet_attestation)
    drift = check_rail_drift(testnet, strict_token(testnet).rpc)
    assert not drift.matches and "differs" in drift.reason

    matching_hash = hashlib.sha256(bytes.fromhex("6000")).hexdigest()
    matching = replace(
        testnet,
        attestation=replace(testnet_attestation, expected_code_sha256=matching_hash),
    )
    assert check_rail_drift(matching, strict_token(matching).rpc).matches


def test_reconciliation_scalar_and_model_validation_paths() -> None:
    with pytest.raises(ReconciliationError):
        _exact_hex("0x" + "gg" * 32, size=32, what="hash")
    with pytest.raises(ReconciliationError, match="quantity"):
        from psv.reconciliation import _quantity as recon_quantity

        recon_quantity("0xg", what="quantity")
    with pytest.raises(ReconciliationError, match="outside uint256"):
        recon_quantity("0x1" + "0" * 64, what="quantity")
    with pytest.raises(ReconciliationError, match="padded"):
        _addr_from_topic("0x" + "11" * 32, what="payer")
    with pytest.raises(ReconciliationError, match="chain_id"):
        SettlementIdentity(0, "0x" + "11" * 20, TX_HASH, 0)
    with pytest.raises(ReconciliationError, match="log_index"):
        SettlementIdentity(1, "0x" + "11" * 20, TX_HASH, -1)

    identity = SettlementIdentity(1, "0x" + "11" * 20, TX_HASH, 0)
    valid = {
        "identity": identity,
        "block_hash": BLOCK_HASH,
        "block_number": 1,
        "payer": PAYER,
        "payee": PAYEE,
        "value": 1,
        "removed": False,
    }
    for field, value in (("block_number", -1), ("value", -1), ("removed", 1)):
        with pytest.raises(ReconciliationError):
            OnChainCredit(**(valid | {field: value}))  # type: ignore[arg-type]
    assert OnChainCredit(**valid).payer_norm == PAYER  # type: ignore[arg-type]
    with pytest.raises(ReconciliationError, match="chain_id"):
        decode_transfer_log({}, chain_id=0)


def _valid_report() -> ReconReport:
    rail = get_rail("mock-anvil")
    return cli.run_reconcile(
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


def test_report_build_and_validation_reject_inconsistent_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _valid_report()
    rail = get_rail("mock-anvil")
    with pytest.raises(ValueError, match="timezone-aware"):
        ReconReport.build(
            rail,
            payer=PAYER,
            payee=PAYEE,
            nonce=NONCE,
            sut_believes_paid=True,
            divergence=SimpleNamespace(  # type: ignore[arg-type]
                kind=SimpleNamespace(value="consistent_paid"),
                severity=SimpleNamespace(value="ok"),
                message="ok",
                is_failure=False,
            ),
            evidence=report.evidence,
            generated_at=datetime(2026, 1, 1),
        )
    with pytest.raises(ValueError, match="selected rail"):
        ReconReport.build(
            rail,
            payer=PAYER,
            payee=PAYEE,
            nonce=NONCE,
            sut_believes_paid=True,
            divergence=SimpleNamespace(  # type: ignore[arg-type]
                kind=SimpleNamespace(value="consistent_paid"),
                severity=SimpleNamespace(value="ok"),
                message="ok",
                is_failure=False,
            ),
            evidence=replace(report.evidence, chain_id=1),
        )

    with pytest.raises(ValueError, match="reason code"):
        replace(report, reason_code="wrong").validate()
    with pytest.raises(ValueError, match="amount"):
        replace(report, evidence=replace(report.evidence, required_amount=0)).validate()
    with pytest.raises(ValueError, match="chain IDs"):
        replace(report, evidence=replace(report.evidence, chain_id=1)).validate()
    assert report.as_dict()["reportVersion"] == "2.0"

    monkeypatch.setattr(ReconReport, "to_dict", lambda self: {})
    with pytest.raises(ValueError, match="envelope"):
        report.validate()


@pytest.mark.parametrize(
    "mutation",
    [
        {"reportVersion": "1.0"},
        {"$schema": "wrong"},
        {"tool": {}},
        {"rail": None},
        {"evidence": {"chainId": 1}},
    ],
)
def test_deserialized_report_validation_rejects_bad_contract(
    mutation: dict[str, object],
) -> None:
    doc = _valid_report().to_dict()
    doc.update(mutation)
    if mutation == {"evidence": {"chainId": 1}}:
        assert isinstance(doc["rail"], dict)
        doc["rail"]["chainId"] = 2  # type: ignore[index]
    with pytest.raises(ValueError):
        validate_report_document(doc)


@pytest.mark.parametrize(
    ("function", "value"),
    [
        (cli._uint256, "not-a-number"),
        (cli._timeout, "not-a-number"),
        (cli._rpc_url, "x" * 2049),
        (cli._rpc_url, "ftp://example.com"),
        (cli._rpc_url, "https://example.com/#fragment"),
        (cli._rpc_url, "https://user:secret@example.com"),
        (cli._output_path, ""),
    ],
)
def test_cli_scalar_validators_reject_hostile_values(function: Any, value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        function(value)


def test_cli_output_and_dispatch_error_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(OSError, match="exceeds"):
        cli._write_report(tmp_path / "too-big", "x" * (2 * 1024 * 1024 + 1), "JSON")

    check = RailDriftCheck(
        rail_key="mock-anvil",
        chain_id=84532,
        block_number=1,
        block_hash=BLOCK_HASH,
        code_sha256="00" * 32,
        expected_code_sha256=None,
        implementation_address=None,
        implementation_code_sha256=None,
        attestation_version="test",
        calibrated=False,
        matches=False,
        reason="uncalibrated",
    )
    monkeypatch.setattr(cli, "check_rail_drift", lambda rail, rpc: check)
    assert (
        cli.main(["rail-drift", "--rail", "mock-anvil", "--rpc-url", "http://127.0.0.1:8545"]) == 1
    )
    monkeypatch.setattr(cli, "_cmd_rail_drift", lambda args: (_ for _ in ()).throw(OSError()))
    assert (
        cli.main(["rail-drift", "--rail", "mock-anvil", "--rpc-url", "http://127.0.0.1:8545"]) == 2
    )


def test_cli_broken_output_is_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_cmd_reconcile", lambda args: (_ for _ in ()).throw(OSError("pipe")))
    argv = [
        "reconcile",
        "--rail",
        "mock-anvil",
        "--payer",
        PAYER,
        "--payee",
        PAYEE,
        "--nonce",
        NONCE,
        "--tx-hash",
        TX_HASH,
        "--log-index",
        "0",
        "--required-amount",
        "1",
        "--payer-before",
        "1",
        "--payee-before",
        "0",
        "--sut-paid",
    ]
    assert cli.main(argv) == 2
