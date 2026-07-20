"""Hypothesis-based fuzz tests for quote parsing and nonce handling (T-21)."""

# tests/test_fuzz_quote.py
from __future__ import annotations

from typing import Any

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

# --- minimal encode/decode under test (mirrors psv quote wire format) ---

CHAIN_IDS = (1, 10, 137, 42161, 8453, 11155111)

_QUOTE_KEYS = ("chain_id", "amount_in", "amount_out", "nonce", "token_in", "token_out")


def encode_quote(
    chain_id: int,
    amount_in: int,
    amount_out: int,
    nonce: int,
    token_in: str,
    token_out: str,
) -> dict[str, Any]:
    if chain_id <= 0:
        raise ValueError("chain_id must be positive")
    if amount_in < 0 or amount_out < 0:
        raise ValueError("amounts must be non-negative")
    if nonce < 0:
        raise ValueError("nonce must be non-negative")
    return {
        "chain_id": int(chain_id),
        "amount_in": int(amount_in),
        "amount_out": int(amount_out),
        "nonce": int(nonce),
        "token_in": token_in.lower(),
        "token_out": token_out.lower(),
    }


def decode_quote(payload: dict[str, Any]) -> dict[str, Any]:
    missing = [k for k in _QUOTE_KEYS if k not in payload]
    if missing:
        raise KeyError(f"missing keys: {missing}")
    return encode_quote(
        chain_id=int(payload["chain_id"]),
        amount_in=int(payload["amount_in"]),
        amount_out=int(payload["amount_out"]),
        nonce=int(payload["nonce"]),
        token_in=str(payload["token_in"]),
        token_out=str(payload["token_out"]),
    )


def next_nonce(prev: int) -> int:
    if prev < 0:
        raise ValueError("nonce must be non-negative")
    return prev + 1


# --- strategies ---

_addr = st.from_regex(r"0x[0-9a-fA-F]{40}", fullmatch=True)
_amount = st.integers(min_value=0, max_value=2**256 - 1)
_nonce = st.integers(min_value=0, max_value=2**64 - 1)
_chain = st.sampled_from(CHAIN_IDS) | st.integers(min_value=1, max_value=2**32 - 1)


@given(
    chain_id=_chain,
    amount_in=_amount,
    amount_out=_amount,
    nonce=_nonce,
    token_in=_addr,
    token_out=_addr,
)
@settings(max_examples=200)
def test_quote_roundtrip(chain_id, amount_in, amount_out, nonce, token_in, token_out):
    raw = encode_quote(chain_id, amount_in, amount_out, nonce, token_in, token_out)
    decoded = decode_quote(raw)
    assert decoded["chain_id"] == chain_id
    assert decoded["amount_in"] == amount_in
    assert decoded["amount_out"] == amount_out
    assert decoded["nonce"] == nonce
    assert decoded["token_in"] == token_in.lower()
    assert decoded["token_out"] == token_out.lower()
    assert decode_quote(decoded) == decoded


@given(amount_in=_amount, amount_out=_amount)
@settings(max_examples=100)
def test_amounts_non_negative(amount_in, amount_out):
    q = encode_quote(1, amount_in, amount_out, 0, "0x" + "11" * 20, "0x" + "22" * 20)
    assert q["amount_in"] >= 0
    assert q["amount_out"] >= 0


@given(chain_id=st.integers(max_value=0))
@settings(max_examples=50)
def test_invalid_chain_id_rejected(chain_id):
    with pytest.raises(ValueError, match="chain_id"):
        encode_quote(chain_id, 1, 1, 0, "0x" + "11" * 20, "0x" + "22" * 20)


@given(chain_id=_chain)
@settings(max_examples=50)
def test_valid_chain_ids_accepted(chain_id):
    q = encode_quote(chain_id, 0, 0, 0, "0x" + "aa" * 20, "0x" + "bb" * 20)
    assert q["chain_id"] == chain_id
    assert isinstance(q["chain_id"], int)
    assert q["chain_id"] > 0


@given(n=_nonce)
@settings(max_examples=100)
def test_nonce_monotonic(n):
    n2 = next_nonce(n)
    assert n2 == n + 1
    assert n2 >= 0


@given(n=st.integers(max_value=-1))
@settings(max_examples=30)
def test_negative_nonce_rejected(n):
    with pytest.raises(ValueError, match="nonce"):
        encode_quote(1, 0, 0, n, "0x" + "11" * 20, "0x" + "22" * 20)


@given(
    amount_in=st.integers(max_value=-1),
    amount_out=_amount,
)
@settings(max_examples=30)
def test_negative_amount_in_rejected(amount_in, amount_out):
    with pytest.raises(ValueError, match="amounts"):
        encode_quote(1, amount_in, amount_out, 0, "0x" + "11" * 20, "0x" + "22" * 20)


@given(payload=st.dictionaries(st.text(), st.integers(), max_size=5))
@settings(max_examples=80)
def test_decode_missing_keys(payload):
    assume(not all(k in payload for k in _QUOTE_KEYS))
    with pytest.raises(KeyError):
        decode_quote(payload)
