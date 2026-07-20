# src/psv/metrics.py
try:
    from prometheus_client import Counter, Histogram, Gauge
    divergence_count = Counter("psv_divergence_count", "Total divergences")
    reconciliation_latency_seconds = Histogram("psv_reconciliation_latency_seconds", "Reconciliation latency")
    settlement_histogram = Histogram("psv_settlement_seconds", "Settlement time")
except ImportError:
    divergence_count = reconciliation_latency_seconds = settlement_histogram = None

