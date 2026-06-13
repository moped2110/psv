"""Offline tests for the load-profile runner (no chain)."""

from __future__ import annotations

import time

import pytest

from psv.load import LoadResult, run_profile


def test_run_profile_counts_and_latencies() -> None:
    seen: list[int] = []

    def task(i: int) -> None:
        time.sleep(0.001)
        seen.append(i)

    res = run_profile(task, iterations=20, concurrency=4)
    assert res.total == 20
    assert res.errors == 0
    assert res.ok == 20
    assert len(seen) == 20
    assert len(res.latencies_ms) == 20
    assert res.p50_ms > 0 and res.p95_ms >= res.p50_ms and res.max_ms >= res.p95_ms
    assert res.throughput_per_s > 0


def test_run_profile_captures_errors() -> None:
    def task(i: int) -> None:
        if i % 2 == 0:
            raise RuntimeError("boom")

    res = run_profile(task, iterations=10, concurrency=1)
    assert res.total == 10
    assert res.errors == 5
    assert res.ok == 5
    assert res.error_rate == 0.5


def test_concurrency_runs_faster_than_serial() -> None:
    def task(i: int) -> None:
        time.sleep(0.01)

    serial = run_profile(task, iterations=8, concurrency=1)
    parallel = run_profile(task, iterations=8, concurrency=8)
    # 8x sleeps serialized (~80ms) vs parallel (~10ms): parallel is clearly faster.
    assert parallel.duration_s < serial.duration_s


def test_invalid_args_rejected() -> None:
    with pytest.raises(ValueError):
        run_profile(lambda i: None, iterations=0)
    with pytest.raises(ValueError):
        run_profile(lambda i: None, iterations=5, concurrency=0)


def test_percentiles_on_empty_are_zero() -> None:
    res = LoadResult(total=0, errors=0)
    assert res.p50_ms == 0.0 and res.max_ms == 0.0 and res.throughput_per_s == 0.0
