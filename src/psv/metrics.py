"""Prometheus metrics (optional)."""
from __future__ import annotations

from typing import Any

try:
    from prometheus_client import Counter, Gauge, Histogram
except ImportError:
    class _Noop:
        def inc(self, *a: Any, **kw: Any) -> None: pass
        def observe(self, *a: Any, **kw: Any) -> None: pass
        def set(self, *a: Any, **kw: Any) -> None: pass
    Counter = Gauge = Histogram = _Noop

divergence_count: Counter = Counter("psv_divergence_total", "Total divergences detected")
reconciliation_latency: Histogram = Histogram("psv_reconciliation_seconds", "Reconciliation duration")
settlement_count: Counter = Counter("psv_settlement_total", "Total settlements verified")
