"""Enforce non-empty docstrings for every production Python function.

The scan intentionally includes methods, async functions, nested functions, and
this checker itself. Tests are excluded so scenario names and fixtures remain
the primary documentation for test-only helpers.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOTS = (ROOT / "src", ROOT / "tools")


@dataclass(frozen=True, order=True)
class MissingFunctionDoc:
    """Location and qualified name of one undocumented function."""

    path: Path
    line: int
    name: str


def python_files(roots: Iterable[Path]) -> list[Path]:
    """Return every Python source file below the supplied scan roots."""
    return sorted(path for root in roots for path in root.rglob("*.py") if path.is_file())


def missing_function_docs(path: Path) -> list[MissingFunctionDoc]:
    """Return every function in one module that lacks a non-empty docstring."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    findings: list[MissingFunctionDoc] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        docstring = ast.get_docstring(node, clean=True)
        if not docstring or not docstring.splitlines()[0].strip():
            findings.append(MissingFunctionDoc(path, node.lineno, node.name))
    return sorted(findings)


def check_function_docs(roots: Iterable[Path] = DEFAULT_ROOTS) -> list[MissingFunctionDoc]:
    """Return missing-docstring findings across all production scan roots."""
    return [finding for path in python_files(roots) for finding in missing_function_docs(path)]


def function_count(roots: Iterable[Path] = DEFAULT_ROOTS) -> int:
    """Count all synchronous and asynchronous functions in the scan roots."""
    total = 0
    for path in python_files(roots):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        total += sum(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree)
        )
    return total


def main() -> int:
    """Report undocumented functions and return a shell-friendly exit status."""
    findings = check_function_docs()
    if findings:
        print("Function documentation check failed:")
        for finding in findings:
            try:
                display = finding.path.relative_to(ROOT)
            except ValueError:
                display = finding.path
            print(f"- {display}:{finding.line}: {finding.name}")
        return 1
    print(f"Function documentation check passed ({function_count()} functions).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
