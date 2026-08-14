"""Opt-in hybrid verification for version 2 facilitator receipts."""

from __future__ import annotations

import base64
import binascii
import copy
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from .provider import ProviderUnavailableError, VerificationProvider, load_provider

_DOMAIN = b"PSV-RECEIPT-V2\x00"
_CLASSICAL_ALG = "ECDSA-P256-SHA256"
_PQC_ALG = "ML-DSA-65"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


class FindingKind(str, Enum):
    """Receipt-specific findings in PSV's stable finding vocabulary."""

    UNVERIFIABLE_RECEIPT = "unverifiable_receipt"
    NAKED_RECEIPT = "naked_receipt"


class VerificationStatus(str, Enum):
    """Outcome of the optional receipt-verification stage."""

    DISABLED = "disabled"
    VERIFIED = "verified"
    FAILED = "failed"


@dataclass(frozen=True)
class PQCConfig:
    """Trusted policy configuration; PQC is disabled unless explicitly enabled."""

    enabled: bool = False
    provider: str = "cryptography"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> PQCConfig:
        """Read ``PSV_PQC`` without accepting ambiguous truthy values."""
        source = os.environ if environ is None else environ
        return cls(enabled=source.get("PSV_PQC", "").strip().lower() in _TRUE_VALUES)


@dataclass(frozen=True)
class ReceiptVerification:
    """Verification verdict while preserving the caller's exact input bytes."""

    status: VerificationStatus
    receipt_bytes: bytes
    finding: FindingKind | None = None
    detail: str = ""


def _unsigned_receipt(receipt: Mapping[str, object]) -> dict[str, object]:
    """Copy a receipt and blank only the two signature values."""
    value = copy.deepcopy(dict(receipt))
    sig_v2 = value.get("sig_v2")
    if not isinstance(sig_v2, dict):
        raise ValueError("sig_v2 must be an object")
    for name in ("classical", "pqc"):
        entry = sig_v2.get(name)
        if not isinstance(entry, dict):
            raise ValueError(f"sig_v2.{name} must be an object")
        entry["signature"] = ""
    return value


def canonical_receipt_payload(receipt: Mapping[str, object]) -> bytes:
    """Return the domain-separated canonical bytes covered by both signatures."""
    unsigned = _unsigned_receipt(receipt)
    _validate_json_profile(unsigned)
    try:
        encoded = json.dumps(
            unsigned,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("receipt is not canonicalizable JSON") from exc
    return _DOMAIN + encoded


def _validate_json_profile(value: object) -> None:
    """Reject values whose cross-language canonical encoding would be ambiguous."""
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or not key.isascii():
                raise ValueError("receipt object keys must be ASCII strings")
            _validate_json_profile(child)
    elif isinstance(value, list):
        for child in value:
            _validate_json_profile(child)
    elif isinstance(value, float):
        raise ValueError("receipt numbers must be integers; decimal amounts use strings")
    elif value is not None and not isinstance(value, (bool, int, str)):
        raise ValueError("receipt contains a non-JSON value")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build a JSON object while rejecting duplicate member names."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member: {key}")
        result[key] = value
    return result


def _decode_signature(value: object) -> bytes:
    """Decode the format's strict unpadded Base64url representation."""
    if not isinstance(value, str) or not value or "=" in value:
        raise ValueError("signature must be non-empty unpadded base64url")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("signature is not valid base64url") from exc


def _signature_entry(sig_v2: Mapping[str, object], name: str, algorithm: str) -> tuple[str, bytes]:
    """Validate one closed signature entry and return its key ID and bytes."""
    entry = sig_v2.get(name)
    if not isinstance(entry, dict) or set(entry) != {"alg", "kid", "signature"}:
        raise ValueError(f"sig_v2.{name} has an invalid structure")
    if entry["alg"] != algorithm:
        raise ValueError(f"sig_v2.{name}.alg is not registered for receipt v2")
    kid = entry["kid"]
    if not isinstance(kid, str) or not kid or len(kid) > 128:
        raise ValueError(f"sig_v2.{name}.kid is invalid")
    return kid, _decode_signature(entry["signature"])


def verify_receipt(
    receipt_bytes: bytes,
    *,
    classical_keys: Mapping[str, bytes],
    pqc_keys: Mapping[str, bytes],
    config: PQCConfig | None = None,
    provider: VerificationProvider | None = None,
) -> ReceiptVerification:
    """Verify both receipt signatures when trusted configuration enables PQC."""
    policy = PQCConfig.from_env() if config is None else config
    if not policy.enabled:
        return ReceiptVerification(VerificationStatus.DISABLED, receipt_bytes)
    try:
        decoded = json.loads(receipt_bytes, object_pairs_hook=_unique_object)
        if not isinstance(decoded, dict):
            raise ValueError("receipt must be a JSON object")
        sig_v2 = decoded.get("sig_v2")
        if sig_v2 is None:
            return ReceiptVerification(
                VerificationStatus.FAILED,
                receipt_bytes,
                FindingKind.NAKED_RECEIPT,
                "PQC policy requires sig_v2",
            )
        if not isinstance(sig_v2, dict) or set(sig_v2) != {"version", "classical", "pqc"}:
            raise ValueError("sig_v2 has an invalid structure")
        if sig_v2["version"] != 2:
            raise ValueError("sig_v2.version must be 2")
        classical_kid, classical_signature = _signature_entry(sig_v2, "classical", _CLASSICAL_ALG)
        pqc_kid, pqc_signature = _signature_entry(sig_v2, "pqc", _PQC_ALG)
        classical_key = classical_keys[classical_kid]
        pqc_key = pqc_keys[pqc_kid]
        if len(classical_key) != 65 or len(pqc_key) != 1952 or len(pqc_signature) != 3309:
            raise ValueError("hybrid key or ML-DSA-65 signature has an invalid length")
        payload = canonical_receipt_payload(decoded)
        verifier = load_provider(policy.provider) if provider is None else provider
        classical_valid = verifier.verify_ecdsa_p256_sha256(
            classical_key, payload, classical_signature
        )
        pqc_valid = verifier.verify_mldsa65(pqc_key, payload, pqc_signature)
        if not (classical_valid and pqc_valid):
            raise ValueError("hybrid verification requires both signatures to be valid")
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
        KeyError,
        ProviderUnavailableError,
        ValueError,
    ) as exc:
        return ReceiptVerification(
            VerificationStatus.FAILED,
            receipt_bytes,
            FindingKind.UNVERIFIABLE_RECEIPT,
            str(exc),
        )
    return ReceiptVerification(VerificationStatus.VERIFIED, receipt_bytes)


__all__ = [
    "FindingKind",
    "PQCConfig",
    "ReceiptVerification",
    "VerificationStatus",
    "canonical_receipt_payload",
    "verify_receipt",
]
