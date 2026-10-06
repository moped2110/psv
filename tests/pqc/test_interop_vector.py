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

from psv.pqc import PQCConfig, VerificationStatus, canonical_receipt_payload, verify_receipt
from psv.pqc.provider import CryptographyProvider

_VECTOR = Path(__file__).parent / "vectors" / "receipt-v2-interop.json"
# Pins the copy itself: the x402-conformance copy is pinned to the same digest.
_VECTOR_FILE_SHA256 = "c5128fea711a2a483333221e82b700650d1e9cadd9c350bf9ce430561c8ceca7"


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


def _verify(vector: dict[str, object], receipt: object) -> VerificationStatus:
    """Run psv's strict hybrid verification over a receipt object."""
    provider = CryptographyProvider()
    if not provider.available:
        pytest.skip(provider.unavailable_reason)
    raw = json.dumps(receipt, ensure_ascii=False).encode("utf-8")
    result = verify_receipt(
        raw,
        classical_keys={"interop-ecdsa-test": _key(vector, "interop-ecdsa-test")},
        pqc_keys={"interop-mldsa-test": _key(vector, "interop-mldsa-test")},
        config=PQCConfig(enabled=True),
        provider=provider,
    )
    return result.status


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
