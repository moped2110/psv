from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from psv.pqc.provider import (
    CryptographyProvider,
    ProviderUnavailableError,
    load_provider,
)


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
