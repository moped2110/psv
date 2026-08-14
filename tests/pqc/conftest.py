from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(scope="session")
def acvp_mldsa65_vectors() -> list[dict[str, Any]]:
    path = Path(__file__).parent / "vectors" / "nist-acvp-mldsa65-sigver.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["source"]["publisher"] == "NIST ACVP Server"
    assert document["algorithm"] == "ML-DSA-65"
    return document["vectors"]
