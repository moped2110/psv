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


@dataclass
class LoadResult:
    total: int
    errors: int
    latencies_ms: list[float] = field(default_factory=list)
    duration_s: float = 0.0

    @property
    def ok(self) -> int:
        return self.total - self.errors

    @property
    def error_rate(self) -> float:
        return self.errors / self.total if self.total else 0.0

    @property
    def throughput_per_s(self) -> float:
        return self.total / self.duration_s if self.duration_s > 0 else 0.0

    def _pct(self, p: float) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        idx = min(len(ordered) - 1, int(round((p / 100.0) * (len(ordered) - 1))))
        return ordered[idx]

    @property
    def p50_ms(self) -> float:
        return self._pct(50)

    @property
    def p95_ms(self) -> float:
        return self._pct(95)

    @property
    def max_ms(self) -> float:
        return max(self.latencies_ms) if self.latencies_ms else 0.0


def run_profile(
    task: Callable[[int], Any],
    *,
    iterations: int,
    concurrency: int = 1,
) -> LoadResult:
    """Run ``task(i)`` for ``i`` in ``range(iterations)`` across ``concurrency``
    workers, timing each call and capturing exceptions as errors."""
    if iterations <= 0:
        raise ValueError("iterations must be > 0")
    if concurrency <= 0:
        raise ValueError("concurrency must be > 0")

    latencies: list[float] = []
    errors = 0

    def _run(i: int) -> float | None:
        start = time.perf_counter()
        try:
            task(i)
        except Exception:
            return None
        return (time.perf_counter() - start) * 1000.0

    started = time.perf_counter()
    if concurrency == 1:
        results = [_run(i) for i in range(iterations)]
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            results = list(ex.map(_run, range(iterations)))
    duration = time.perf_counter() - started

    for r in results:
        if r is None:
            errors += 1
        else:
            latencies.append(r)

    return LoadResult(total=iterations, errors=errors, latencies_ms=latencies, duration_s=duration)
