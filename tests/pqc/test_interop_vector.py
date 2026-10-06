"""The shared receipt-v2 vector that x402-conformance verifies too.

x402-conformance re-implements receipt-v2 canonicalization in its PQC checks rather
than importing psv, so nothing would notice if the two drifted. Both repositories carry
a byte-identical copy of ``receipt-v2-interop.json`` (here ``tests/pqc/vectors/``, in
x402-conformance ``tests/fixtures/pqc/``) and verify it with their own code: a change to
either canonicalization breaks the recorded hash in that repository.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest

from psv.pqc import (
    FindingKind,
    PQCConfig,
    ReceiptVerification,
    VerificationStatus,
    canonical_receipt_payload,
    verify_receipt,
)
from psv.pqc.provider import CryptographyProvider

_VECTOR = Path(__file__).parent / "vectors" / "receipt-v2-interop.json"
# Pins the copy itself: the x402-conformance copy is pinned to the same digest.
_VECTOR_FILE_SHA256 = "b8d8621efc7c0469b546853a6d150cc81a46e9dda9d7b77817990d9e27021e8a"


def _load() -> dict[str, object]:
    """Read the vector and fail loudly if this copy was edited on its own."""
    raw = _VECTOR.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _VECTOR_FILE_SHA256, (
        "receipt-v2-interop.json changed; update the x402-conformance copy in the same change"
    )
    vector = json.loads(raw)
    assert isinstance(vector, dict)
    return vector


def _key(vector: dict[str, object], kid: str) -> bytes:
    """Decode one unpadded base64url public key from the vector."""
    keys = vector["keys"]
    assert isinstance(keys, dict)
    value = keys[kid]
    assert isinstance(value, str)
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _verify_bytes(vector: dict[str, object], raw: bytes) -> ReceiptVerification:
    """Run psv's strict hybrid verification over receipt bytes."""
    provider = CryptographyProvider()
    if not provider.available:
        pytest.skip(provider.unavailable_reason)
    return verify_receipt(
        raw,
        classical_keys={"interop-ecdsa-test": _key(vector, "interop-ecdsa-test")},
        pqc_keys={"interop-mldsa-test": _key(vector, "interop-mldsa-test")},
        config=PQCConfig(enabled=True),
        provider=provider,
    )


def _verify(vector: dict[str, object], receipt: object) -> VerificationStatus:
    """Run psv's strict hybrid verification over a receipt object."""
    raw = json.dumps(receipt, ensure_ascii=False).encode("utf-8")
    return _verify_bytes(vector, raw).status


def _rejected_cases() -> list[dict[str, str]]:
    """The shared texts both implementations must refuse, read at collection time."""
    rejected = json.loads(_VECTOR.read_bytes())["rejected"]
    assert isinstance(rejected, list) and rejected
    return rejected


def test_canonical_bytes_match_the_shared_digest() -> None:
    """psv's canonical bytes hash to the digest x402-conformance also asserts."""
    vector = _load()
    receipt = vector["receipt"]
    assert isinstance(receipt, dict)
    digest = hashlib.sha256(canonical_receipt_payload(receipt)).hexdigest()
    assert digest == vector["canonical_sha256"]


def test_shared_vector_verifies() -> None:
    """Both signatures in the shared vector verify under psv's strict policy."""
    vector = _load()
    assert _verify(vector, vector["receipt"]) is VerificationStatus.VERIFIED


def test_shared_vector_rejects_a_changed_business_field() -> None:
    """Changing a covered non-ASCII field invalidates the shared vector."""
    vector = _load()
    receipt = copy.deepcopy(vector["receipt"])
    assert isinstance(receipt, dict)
    receipt["memo"] = "Cafe"
    assert _verify(vector, receipt) is VerificationStatus.FAILED


@pytest.mark.parametrize("case", _rejected_cases(), ids=lambda case: case["name"])
def test_shared_rejections_fail_with_the_shared_error(case: dict[str, str]) -> None:
    """Floats, NaN, repeated and non-ASCII member names are refused with the shared text.

    x402-conformance asserts the same error for the same text, so the two
    implementations agree on what is *not* a receipt, not only on what is.
    """
    vector = _load()
    result = _verify_bytes(vector, case["receipt_json"].encode("utf-8"))
    assert result.status is VerificationStatus.FAILED
    assert result.finding is FindingKind.UNVERIFIABLE_RECEIPT
    assert result.detail == case["error"]
