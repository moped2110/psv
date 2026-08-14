from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from psv.pqc.provider import (
    CryptographyProvider,
    ProviderUnavailableError,
    load_provider,
)


@pytest.fixture(scope="session")
def acvp_mldsa65_vectors() -> list[dict[str, Any]]:
    path = Path(__file__).parent / "vectors" / "nist-acvp-mldsa65-sigver.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["source"]["publisher"] == "NIST ACVP Server"
    assert document["algorithm"] == "ML-DSA-65"
    return document["vectors"]


def test_nist_acvp_mldsa65_sigver(acvp_mldsa65_vectors: list[dict[str, Any]]) -> None:
    provider = CryptographyProvider()
    if not provider.available:
        pytest.skip(provider.unavailable_reason)
    for vector in acvp_mldsa65_vectors:
        actual = provider.verify_mldsa65(
            bytes.fromhex(vector["pk"]),
            bytes.fromhex(vector["message"]),
            bytes.fromhex(vector["signature"]),
            context=bytes.fromhex(vector["context"]),
        )
        assert actual is vector["testPassed"], f"NIST ACVP tcId={vector['tcId']}"


def test_primary_provider_runtime_detection_is_descriptive() -> None:
    provider = CryptographyProvider()
    assert provider.available or "OpenSSL 3.5" in provider.unavailable_reason


def test_openssl_before_35_is_detected_without_crash() -> None:
    with patch.object(
        CryptographyProvider,
        "_openssl_runtime",
        return_value=(0x30409000, "OpenSSL 3.4.9"),
    ):
        provider = CryptographyProvider()
    assert provider.available is False
    assert "OpenSSL 3.5" in provider.unavailable_reason


def test_nist_acvp_mldsa65_sigver_optional_oqs(
    acvp_mldsa65_vectors: list[dict[str, Any]],
) -> None:
    try:
        provider = load_provider("oqs")
    except ProviderUnavailableError as exc:
        pytest.skip(str(exc))
    for vector in acvp_mldsa65_vectors:
        if vector["context"]:
            continue
        actual = provider.verify_mldsa65(
            bytes.fromhex(vector["pk"]),
            bytes.fromhex(vector["message"]),
            bytes.fromhex(vector["signature"]),
        )
        assert actual is vector["testPassed"], f"NIST ACVP tcId={vector['tcId']}"


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ProviderUnavailableError, match="unknown PQC provider"):
        load_provider("not-a-provider")


def test_oqs_import_is_confined_to_provider_package() -> None:
    root = Path(__file__).parents[2] / "src" / "psv"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if path.parent.name == "provider":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(alias.name == "oqs" for alias in node.names):
                offenders.append(str(path))
            if isinstance(node, ast.ImportFrom) and node.module == "oqs":
                offenders.append(str(path))
    assert offenders == []
