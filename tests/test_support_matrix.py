from __future__ import annotations

import json

from tools.validate_support_matrix import REGISTRY, validate_registry


def test_public_support_matrix_is_valid() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert validate_registry(registry) == []


def test_duplicate_ids_are_rejected() -> None:
    scenario = {
        "id": "DUPLICATE",
        "phase": "phase-1",
        "severity": "P0",
        "test": None,
        "environment": "offline",
        "status": "planned",
    }
    registry = {"schemaVersion": "1.0", "scenarios": [scenario, scenario.copy()]}
    assert "duplicate scenario id: DUPLICATE" in validate_registry(registry)
