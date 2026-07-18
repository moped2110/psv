"""Offline tests for the load-profile runner (no chain)."""

from __future__ import annotations

import time

import pytest

from psv.load import (
    FacilitatorTaskPool,
    LoadResult,
    LoadStage,
    run_profile,
    run_staged_profile,
    standard_profile,
)


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
    assert res.attempted_throughput_per_s > res.successful_throughput_per_s > 0
    assert [sample.iteration for sample in res.error_samples] == [0, 2, 4, 6, 8]
    assert {sample.exception_type for sample in res.error_samples} == {"RuntimeError"}
    assert {sample.message for sample in res.error_samples} == {"boom"}


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
    with pytest.raises(ValueError):
        run_profile(lambda i: None, iterations=5, max_error_samples=-1)


def test_percentiles_on_empty_are_zero() -> None:
    res = LoadResult(total=0, errors=0)
    assert res.p50_ms == 0.0 and res.max_ms == 0.0 and res.throughput_per_s == 0.0


def test_error_samples_are_bounded_and_serializable() -> None:
    def fail(i: int) -> None:
        raise ValueError(f"bad\niteration {i}")

    result = run_profile(fail, iterations=20, concurrency=4, max_error_samples=3)
    assert result.errors == 20
    assert len(result.error_samples) == 3
    evidence = result.as_dict()
    assert evidence["attempted"] == 20
    assert evidence["successful"] == 0
    assert evidence["errorSamples"][0]["message"] == "bad iteration 0"


def test_staged_profile_preserves_unique_iteration_ids_and_recovery() -> None:
    seen: list[int] = []
    stages = [LoadStage("spike", 4, 2), LoadStage("recovery", 2, 1)]
    result = run_staged_profile(seen.append, stages)
    assert sorted(seen) == list(range(6))
    assert result.recovered is True
    assert result.total == result.ok == 6 and result.errors == 0
    assert result.as_dict()["correctness"] == {
        "attempted": 6,
        "successful": 6,
        "errors": 0,
    }
    assert [stage["name"] for stage in result.as_dict()["stages"]] == ["spike", "recovery"]


def test_standard_profile_contains_all_diagnostic_stages() -> None:
    stages = standard_profile(scale=2, peak_concurrency=4)
    assert [stage.name for stage in stages] == [
        "ramp",
        "spike",
        "soak",
        "breakpoint",
        "recovery",
    ]


def test_facilitator_pool_round_robins_independent_workers() -> None:
    calls: list[tuple[int, int]] = []

    def worker(worker_id: int):
        return lambda iteration: calls.append((worker_id, iteration))

    pool = FacilitatorTaskPool(tuple(worker(index) for index in range(3)))
    run_profile(pool, iterations=9, concurrency=3)
    assert sorted(calls) == sorted((index % 3, index) for index in range(9))


def test_facilitator_pool_rejects_empty_worker_set() -> None:
    with pytest.raises(ValueError, match="facilitator"):
        FacilitatorTaskPool(())
