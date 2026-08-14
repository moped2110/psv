from __future__ import annotations

import base64
import json

import pytest

from psv.pqc import (
    FindingKind,
    PQCConfig,
    VerificationStatus,
    canonical_receipt_payload,
    verify_receipt,
)
from psv.pqc.provider import CryptographyProvider

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, mldsa
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


@pytest.fixture()
def signed_receipt() -> tuple[bytes, dict[str, object], dict[str, bytes]]:
    provider = CryptographyProvider()
    if not provider.available:
        pytest.skip(provider.unavailable_reason)
    classical_private = ec.generate_private_key(ec.SECP256R1())
    pqc_private = mldsa.MLDSA65PrivateKey.generate()
    receipt: dict[str, object] = {
        "payment_id": "pay-123",
        "amount": "100",
        "asset": "USDC",
        "chain_id": 8453,
        "settled_at": "2026-08-14T12:00:00Z",
        "sig_v2": {
            "version": 2,
            "classical": {
                "alg": "ECDSA-P256-SHA256",
                "kid": "ecdsa-test",
                "signature": "",
            },
            "pqc": {"alg": "ML-DSA-65", "kid": "mldsa-test", "signature": ""},
        },
    }
    payload = canonical_receipt_payload(receipt)
    sig_v2 = receipt["sig_v2"]
    assert isinstance(sig_v2, dict)
    classical = sig_v2["classical"]
    pqc = sig_v2["pqc"]
    assert isinstance(classical, dict) and isinstance(pqc, dict)
    classical["signature"] = _b64(classical_private.sign(payload, ec.ECDSA(hashes.SHA256())))
    pqc["signature"] = _b64(pqc_private.sign(payload))
    raw = json.dumps(receipt, separators=(",", ":"), ensure_ascii=False).encode()
    classical_public = classical_private.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint
    )
    pqc_public = pqc_private.public_key().public_bytes_raw()
    return raw, receipt, {"ecdsa-test": classical_public, "mldsa-test": pqc_public}


def _verify(raw: bytes, keys: dict[str, bytes]):
    return verify_receipt(
        raw,
        classical_keys={"ecdsa-test": keys["ecdsa-test"]},
        pqc_keys={"mldsa-test": keys["mldsa-test"]},
        config=PQCConfig(enabled=True),
    )


def test_feature_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PSV_PQC", raising=False)
    assert PQCConfig.from_env().enabled is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_feature_flag_accepts_explicit_true(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("PSV_PQC", value)
    assert PQCConfig.from_env().enabled is True


def test_flag_off_is_byte_identical(
    signed_receipt: tuple[bytes, dict[str, object], dict[str, bytes]],
) -> None:
    raw, _, _ = signed_receipt
    result = verify_receipt(raw, classical_keys={}, pqc_keys={}, config=PQCConfig())
    assert result.status is VerificationStatus.DISABLED
    assert result.receipt_bytes == raw
    assert result.finding is None


def test_hybrid_receipt_positive(
    signed_receipt: tuple[bytes, dict[str, object], dict[str, bytes]],
) -> None:
    raw, _, keys = signed_receipt
    result = _verify(raw, keys)
    assert result.status is VerificationStatus.VERIFIED
    assert result.finding is None


def test_downgrade_pqc_field_stripped_is_naked_receipt(
    signed_receipt: tuple[bytes, dict[str, object], dict[str, bytes]],
) -> None:
    _, receipt, keys = signed_receipt
    receipt.pop("sig_v2")
    result = _verify(json.dumps(receipt).encode(), keys)
    assert result.finding is FindingKind.NAKED_RECEIPT


def test_downgrade_mldsa_signature_manipulated_is_unverifiable_receipt(
    signed_receipt: tuple[bytes, dict[str, object], dict[str, bytes]],
) -> None:
    _, receipt, keys = signed_receipt
    pqc = receipt["sig_v2"]["pqc"]  # type: ignore[index]
    signature = bytearray(base64.urlsafe_b64decode(pqc["signature"] + "=="))  # type: ignore[index]
    signature[-1] ^= 1
    pqc["signature"] = _b64(bytes(signature))  # type: ignore[index]
    result = _verify(json.dumps(receipt).encode(), keys)
    assert result.finding is FindingKind.UNVERIFIABLE_RECEIPT


def test_downgrade_algorithm_id_swapped_is_unverifiable_receipt(
    signed_receipt: tuple[bytes, dict[str, object], dict[str, bytes]],
) -> None:
    _, receipt, keys = signed_receipt
    receipt["sig_v2"]["pqc"]["alg"] = "ML-DSA-44"  # type: ignore[index]
    result = _verify(json.dumps(receipt).encode(), keys)
    assert result.finding is FindingKind.UNVERIFIABLE_RECEIPT


def test_classical_signature_manipulated_is_unverifiable_receipt(
    signed_receipt: tuple[bytes, dict[str, object], dict[str, bytes]],
) -> None:
    _, receipt, keys = signed_receipt
    classical = receipt["sig_v2"]["classical"]  # type: ignore[index]
    signature = bytearray(base64.urlsafe_b64decode(classical["signature"] + "=="))  # type: ignore[index]
    signature[-1] ^= 1
    classical["signature"] = _b64(bytes(signature))  # type: ignore[index]
    result = _verify(json.dumps(receipt).encode(), keys)
    assert result.finding is FindingKind.UNVERIFIABLE_RECEIPT


def test_unknown_key_id_is_unverifiable_receipt(
    signed_receipt: tuple[bytes, dict[str, object], dict[str, bytes]],
) -> None:
    raw, _, keys = signed_receipt
    result = verify_receipt(
        raw,
        classical_keys={},
        pqc_keys={"mldsa-test": keys["mldsa-test"]},
        config=PQCConfig(enabled=True),
    )
    assert result.finding is FindingKind.UNVERIFIABLE_RECEIPT


def test_malformed_json_is_unverifiable_receipt() -> None:
    result = verify_receipt(
        b"{",
        classical_keys={},
        pqc_keys={},
        config=PQCConfig(enabled=True),
    )
    assert result.finding is FindingKind.UNVERIFIABLE_RECEIPT


def test_duplicate_json_member_is_unverifiable_receipt() -> None:
    raw = b'{"sig_v2":null,"sig_v2":null}'
    result = verify_receipt(
        raw,
        classical_keys={},
        pqc_keys={},
        config=PQCConfig(enabled=True),
    )
    assert result.finding is FindingKind.UNVERIFIABLE_RECEIPT


def test_v1_receipt_remains_accepted_when_pqc_not_required() -> None:
    raw = b'{"version":1,"signature":"legacy"}'
    result = verify_receipt(raw, classical_keys={}, pqc_keys={}, config=PQCConfig(enabled=False))
    assert result.status is VerificationStatus.DISABLED
    assert result.receipt_bytes == raw
