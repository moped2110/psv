"""Load / stress profiles (Phase 5, ST-class).

A small, dependency-free load runner: drive a target callable concurrently for a
number of iterations and report throughput and latency percentiles, plus any
errors. The point at system level is not raw speed but **correctness under load**
— so a profile run returns enough detail (per-iteration latency, error count) for
a test to assert both performance and invariants (e.g. every payment settled
exactly once).

Kept out of the standard test run behind the ``load`` marker — load tests are
slow and meant for a dev machine, not CI.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LoadErrorSample:
    """One bounded exception sample captured during a load run."""

    iteration: int
    exception_type: str
    message: str


@dataclass
class LoadResult:
    """Aggregate correctness, throughput, latency, and error evidence for one stage."""

    total: int
    errors: int
    latencies_ms: list[float] = field(default_factory=list)
    duration_s: float = 0.0
    error_samples: list[LoadErrorSample] = field(default_factory=list)

    @property
    def ok(self) -> int:
        """Return the number of successful task attempts."""
        return self.total - self.errors

    @property
    def error_rate(self) -> float:
        """Return failed attempts as a fraction of all attempts."""
        return self.errors / self.total if self.total else 0.0

    @property
    def attempted_throughput_per_s(self) -> float:
        """Return attempted operations per elapsed second."""
        return self.total / self.duration_s if self.duration_s > 0 else 0.0

    @property
    def successful_throughput_per_s(self) -> float:
        """Return successful operations per elapsed second."""
        return self.ok / self.duration_s if self.duration_s > 0 else 0.0

    @property
    def throughput_per_s(self) -> float:
        """Backward-compatible alias for attempted throughput."""
        return self.attempted_throughput_per_s

    def _pct(self, p: float) -> float:
        """Select a nearest-rank latency percentile from successful samples."""
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        idx = min(len(ordered) - 1, int(round((p / 100.0) * (len(ordered) - 1))))
        return ordered[idx]

    @property
    def p50_ms(self) -> float:
        """Return median successful-call latency in milliseconds."""
        return self._pct(50)

    @property
    def p95_ms(self) -> float:
        """Return p95 successful-call latency in milliseconds."""
        return self._pct(95)

    @property
    def max_ms(self) -> float:
        """Return maximum successful-call latency in milliseconds."""
        return max(self.latencies_ms) if self.latencies_ms else 0.0

    def as_dict(self) -> dict[str, Any]:
        """Return stable, JSON-serializable load evidence."""
        return {
            "attempted": self.total,
            "successful": self.ok,
            "errors": self.errors,
            "errorRate": self.error_rate,
            "durationSeconds": self.duration_s,
            "attemptedThroughputPerSecond": self.attempted_throughput_per_s,
            "successfulThroughputPerSecond": self.successful_throughput_per_s,
            "latencyMilliseconds": {
                "p50": self.p50_ms,
                "p95": self.p95_ms,
                "max": self.max_ms,
            },
            "errorSamples": [
                {
                    "iteration": sample.iteration,
                    "type": sample.exception_type,
                    "message": sample.message,
                }
                for sample in self.error_samples
            ],
        }


@dataclass(frozen=True)
class LoadStage:
    """One stage of a ramp/spike/soak/recovery profile."""

    name: str
    iterations: int
    concurrency: int


@dataclass
class StagedLoadResult:
    """Aggregate results from a named sequence of load stages."""

    stages: list[tuple[LoadStage, LoadResult]]

    @property
    def total(self) -> int:
        """Return attempted operations across all stages."""
        return sum(result.total for _, result in self.stages)

    @property
    def errors(self) -> int:
        """Return failed operations across all stages."""
        return sum(result.errors for _, result in self.stages)

    @property
    def ok(self) -> int:
        """Return successful operations across all stages."""
        return self.total - self.errors

    @property
    def recovered(self) -> bool:
        """Return whether the final recovery stage completed without errors."""
        return (
            bool(self.stages)
            and self.stages[-1][0].name == "recovery"
            and self.stages[-1][1].errors == 0
        )

    def as_dict(self) -> dict[str, Any]:
        """Return stable JSON-serializable evidence for the staged run."""
        return {
            "recovered": self.recovered,
            "correctness": {
                "attempted": self.total,
                "successful": self.ok,
                "errors": self.errors,
            },
            "stages": [
                {"name": stage.name, "concurrency": stage.concurrency, **result.as_dict()}
                for stage, result in self.stages
            ],
        }


@dataclass(frozen=True)
class FacilitatorTaskPool:
    """Round-robin tasks across independent facilitator/nonce domains."""

    workers: tuple[Callable[[int], Any], ...]

    def __post_init__(self) -> None:
        """Require at least one independent facilitator worker."""
        if not self.workers:
            raise ValueError("at least one facilitator worker is required")

    def __call__(self, iteration: int) -> Any:
        """Route an iteration deterministically to a facilitator worker."""
        return self.workers[iteration % len(self.workers)](iteration)


def run_profile(
    task: Callable[[int], Any],
    *,
    iterations: int,
    concurrency: int = 1,
    max_error_samples: int = 10,
) -> LoadResult:
    """Run ``task(i)`` for ``i`` in ``range(iterations)`` across ``concurrency``
    workers, timing each call and capturing exceptions as errors."""
    if iterations <= 0:
        raise ValueError("iterations must be > 0")
    if concurrency <= 0:
        raise ValueError("concurrency must be > 0")
    if max_error_samples < 0:
        raise ValueError("max_error_samples must be >= 0")

    latencies: list[float] = []
    errors = 0

    def _run(i: int) -> tuple[float | None, LoadErrorSample | None]:
        """Time one task invocation and convert exceptions to bounded evidence."""
        start = time.perf_counter()
        try:
            task(i)
        except Exception as exc:
            message = " ".join(str(exc).split())[:512]
            return None, LoadErrorSample(i, type(exc).__name__, message)
        return (time.perf_counter() - start) * 1000.0, None

    started = time.perf_counter()
    if concurrency == 1:
        results = [_run(i) for i in range(iterations)]
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            results = list(ex.map(_run, range(iterations)))
    duration = time.perf_counter() - started

    error_samples: list[LoadErrorSample] = []
    for latency, error in results:
        if error is not None:
            errors += 1
            if len(error_samples) < max_error_samples:
                error_samples.append(error)
        else:
            assert latency is not None
            latencies.append(latency)

    return LoadResult(
        total=iterations,
        errors=errors,
        latencies_ms=latencies,
        duration_s=duration,
        error_samples=error_samples,
    )


def _with_offset(task: Callable[[int], Any], offset: int) -> Callable[[int], Any]:
    """Wrap a task so staged iteration identifiers remain globally unique."""

    def offset_task(index: int) -> Any:
        """Invoke the wrapped task with the stage's global iteration offset."""
        return task(offset + index)

    return offset_task


def run_staged_profile(
    task: Callable[[int], Any],
    stages: list[LoadStage],
    *,
    max_error_samples: int = 10,
) -> StagedLoadResult:
    """Run named load stages while keeping iteration identifiers unique."""
    if not stages:
        raise ValueError("at least one load stage is required")
    offset = 0
    results: list[tuple[LoadStage, LoadResult]] = []
    for stage in stages:
        if not stage.name.strip():
            raise ValueError("load stage name must not be empty")
        result = run_profile(
            _with_offset(task, offset),
            iterations=stage.iterations,
            concurrency=stage.concurrency,
            max_error_samples=max_error_samples,
        )
        results.append((stage, result))
        offset += stage.iterations
    return StagedLoadResult(results)


def standard_profile(*, scale: int = 10, peak_concurrency: int = 8) -> list[LoadStage]:
    """Return a bounded ramp/spike/soak/recovery profile for opt-in load runs."""
    if scale <= 0:
        raise ValueError("scale must be > 0")
    if peak_concurrency <= 1:
        raise ValueError("peak_concurrency must be > 1")
    return [
        LoadStage("ramp", scale, max(1, peak_concurrency // 2)),
        LoadStage("spike", scale, peak_concurrency),
        LoadStage("soak", scale * 5, max(1, peak_concurrency // 2)),
        LoadStage("breakpoint", scale, peak_concurrency * 2),
        LoadStage("recovery", scale, 1),
    ]
