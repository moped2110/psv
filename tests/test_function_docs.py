"""Regression tests for the production function-documentation gate."""

from __future__ import annotations

from pathlib import Path

from tools.check_function_docs import check_function_docs, function_count


def test_production_functions_have_docstrings() -> None:
    assert function_count() > 0
    assert check_function_docs() == []


def test_checker_finds_methods_async_and_nested_functions(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text(
        "class Sample:\n"
        "    def method(self):\n"
        "        pass\n"
        "async def outer():\n"
        "    def nested():\n"
        "        pass\n",
        encoding="utf-8",
    )

    assert [finding.name for finding in check_function_docs((tmp_path,))] == [
        "method",
        "outer",
        "nested",
    ]
