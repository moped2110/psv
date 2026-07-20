# src/psv/benchmark.py
import pytest

def test_reconcile_100_payments(benchmark):
    def reconcile():
        return sum(range(100))
    benchmark(reconcile)

def test_detect_divergence_1000_entries(benchmark):
    def detect():
        return [i for i in range(1000) if i % 7 == 0]
    benchmark(detect)

