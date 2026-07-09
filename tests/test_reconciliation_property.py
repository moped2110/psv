"""Property-based tests for reconciliation (Hypothesis).

find_unreconciled is the silent-loss defense: on-chain credits with no ledger
record. We assert its invariants across arbitrary log sets and ledgers — it is
exactly the ledger-complement, order-preserving, case-insensitive on tx hashes,
and monotone (recording a payment can only shrink the unreconciled set).
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from psv.reconciliation import (
    TOPIC_TRANSFER,
    decode_transfer_log,
    find_unreconciled,
    topic_addr,
)

MERCHANT = "0x" + "11" * 20

_addr = st.integers(min_value=0, max_value=2**160 - 1).map(lambda n: "0x" + f"{n:040x}")
_uint = st.integers(min_value=0, max_value=2**256 - 1)
_hex32 = st.integers(min_value=0, max_value=2**256 - 1).map(lambda n: "0x" + f"{n:064x}")


@st.composite
def _transfer_log(draw: st.DrawFn) -> dict[str, object]:
    payer = draw(_addr)
    value = draw(_uint)
    tx = draw(_hex32)
    return {
        "topics": [TOPIC_TRANSFER, topic_addr(payer), topic_addr(MERCHANT)],
        "data": hex(value),
        "transactionHash": tx,
    }


_logs = st.lists(_transfer_log(), max_size=25)


@given(payer=_addr, value=_uint, tx=_hex32)
def test_decode_is_faithful(payer: str, value: int, tx: str) -> None:
    log = {
        "topics": [TOPIC_TRANSFER, topic_addr(payer), topic_addr(MERCHANT)],
        "data": hex(value),
        "transactionHash": tx,
    }
    credit = decode_transfer_log(log)
    assert credit.value == value
    assert credit.payer.lower() == payer.lower()
    assert credit.tx_hash == tx.lower()


@given(logs=_logs, data=st.data())
def test_unreconciled_is_the_ledger_complement_in_order(logs, data) -> None:
    all_tx = [str(log["transactionHash"]).lower() for log in logs]
    ledger: set[str] = set()
    if all_tx:
        ledger = set(data.draw(st.lists(st.sampled_from(all_tx), max_size=len(all_tx))))

    result = find_unreconciled(logs, ledger)
    known = {h.lower() for h in ledger}
    expected = [t for t in all_tx if t not in known]

    # exactly the complement, and in original log order (a subsequence of inputs).
    assert [c.tx_hash for c in result] == expected
    assert len(result) <= len(logs)


@given(logs=_logs)
def test_ledger_matching_is_case_insensitive(logs) -> None:
    all_tx = [str(log["transactionHash"]).lower() for log in logs]
    if not all_tx:
        return
    lower = {all_tx[0]}
    upper = {all_tx[0].upper()}
    assert [c.tx_hash for c in find_unreconciled(logs, lower)] == [
        c.tx_hash for c in find_unreconciled(logs, upper)
    ]


@given(logs=_logs, extra=_hex32)
def test_recording_a_payment_never_grows_the_gap(logs, extra: str) -> None:
    # Monotonicity: adding any tx hash to the ledger can only remove entries.
    base = {c.tx_hash for c in find_unreconciled(logs, set())}
    with_extra = {c.tx_hash for c in find_unreconciled(logs, {extra})}
    assert with_extra <= base
