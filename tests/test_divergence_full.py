# tests/test_divergence_full.py
from __future__ import annotations

import pytest
from psv.divergence import detect_divergences
from psv.chain import Chain, Payment

def test_empty_chain_truth():
    assert detect_divergences(Chain([]), Chain([Payment(1)])) == []

def test_empty_system_belief():
    c = Chain([Payment(1), Payment(2)])
    assert len(detect_divergences(c, Chain([]))) == 2

def test_matching():
    c = Chain([Payment(1, nonce=10, asset="ETH")])
    assert detect_divergences(c, c) == []

def test_single_missing():
    truth = Chain([Payment(1), Payment(2)])
    belief = Chain([Payment(1)])
    assert len(detect_divergences(truth, belief)) == 1

def test_large_nonce_drift():
    truth = Chain([Payment(1, nonce=1000)])
    belief = Chain([Payment(1, nonce=1)])
    assert len(detect_divergences(truth, belief)) == 1

def test_asset_mismatch():
    truth = Chain([Payment(1, nonce=5, asset="ETH")])
    belief = Chain([Payment(1, nonce=5, asset="BTC")])
    assert len(detect_divergences(truth, belief)) == 1

