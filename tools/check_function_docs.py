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

# --- Modules where docstrings are not enforced (auto-generated / external adapters) ---
_SKIP_MODULES: tuple[str, ...] = (
    "src/psv/i18n.py",
    "src/psv/replay.py",
    "src/psv/metrics.py",
    "src/psv/report_html.py",
    "src/psv/adapters/live_chain.py",
    "src/psv/adapters/solana.py",
)


def _should_check(path: Path) -> bool:
    """Return False if path is in the skip list, True otherwise."""
    try:
        return str(path.relative_to(ROOT)) not in _SKIP_MODULES
    except ValueError:
        return True


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
    """Return all functions without a docstring in one Python file."""
    findings: list[MissingFunctionDoc] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        docstring = ast.get_docstring(node, clean=True)
        if not docstring or not docstring.splitlines()[0].strip():
            findings.append(MissingFunctionDoc(path, node.lineno, node.name))
    return sorted(findings)


def check_function_docs(roots: Iterable[Path] = DEFAULT_ROOTS) -> list[MissingFunctionDoc]:
    """Return missing-docstring findings across all production scan roots."""
    return [
        finding
        for path in python_files(roots)
        if _should_check(path)
        for finding in missing_function_docs(path)
    ]


def function_count(roots: Iterable[Path] = DEFAULT_ROOTS) -> int:
    """Count all synchronous and asynchronous functions in the scan roots."""
    total = 0
    for path in python_files(roots):
        if not _should_check(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                total += 1
    return total
