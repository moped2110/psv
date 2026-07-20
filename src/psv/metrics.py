"""Prometheus metrics (optional)."""
try:
    from prometheus_client import Counter, Gauge, Histogram
except ImportError:
    class _Noop:
        def inc(self, *a, **kw): pass
        def observe(self, *a, **kw): pass
        def set(self, *a, **kw): pass
    Counter = Gauge = Histogram = _Noop

divergence_count = Counter("psv_divergence_total", "Total divergences detected")
reconciliation_latency = Histogram("psv_reconciliation_seconds", "Reconciliation duration")
settlement_count = Counter("psv_settlement_total", "Total settlements verified")
