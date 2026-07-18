"""Validate the public support matrix and its pytest selectors."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "support-matrix.json"
REQUIRED_FIELDS = {"id", "phase", "severity", "test", "environment", "status"}
STATUSES = {"implemented", "passive", "planned", "out-of-scope"}


def _test_exists(selector: str) -> bool:
    """Return whether a pytest selector names a top-level test function."""
    try:
        relative, function = selector.split("::", maxsplit=1)
    except ValueError:
        return False
    path = ROOT / relative
    if not relative.startswith("tests/") or not path.is_file() or not function.startswith("test_"):
        return False
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function
        for node in tree.body
    )


def validate_registry(registry: dict[str, Any]) -> list[str]:
    """Return structural and selector errors from a support-matrix document."""
    errors: list[str] = []
    if registry.get("schemaVersion") != "1.0":
        errors.append("unsupported schemaVersion")
    scenarios = registry.get("scenarios")
    if not isinstance(scenarios, list):
        return [*errors, "scenarios must be a list"]
    seen: set[str] = set()
    for index, raw in enumerate(scenarios):
        if not isinstance(raw, dict):
            errors.append(f"scenario {index} must be an object")
            continue
        missing = REQUIRED_FIELDS - raw.keys()
        if missing:
            errors.append(f"scenario {index} is missing {sorted(missing)}")
            continue
        scenario_id = raw["id"]
        if not isinstance(scenario_id, str) or not scenario_id:
            errors.append(f"scenario {index} has invalid id")
        elif scenario_id in seen:
            errors.append(f"duplicate scenario id: {scenario_id}")
        else:
            seen.add(scenario_id)
        status = raw["status"]
        if status not in STATUSES:
            errors.append(f"{scenario_id}: unsupported status {status!r}")
        selector = raw["test"]
        if status in {"implemented", "passive"}:
            if not isinstance(selector, str) or not _test_exists(selector):
                errors.append(f"{scenario_id}: registered test does not exist: {selector!r}")
        elif selector is not None:
            errors.append(f"{scenario_id}: non-shipped scenarios must use a null test")
    return errors


def main() -> int:
    """Validate the checked-in support matrix and report a CLI exit status."""
    registry: dict[str, Any] = json.loads(REGISTRY.read_text(encoding="utf-8"))
    errors = validate_registry(registry)
    if errors:
        print("Support-matrix validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Support-matrix validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
