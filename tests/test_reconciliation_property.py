"""Property tests for exact-identity ledger complement semantics."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st
from reconcile_fakes import PAYEE, transfer_log

from psv.reconciliation import SettlementIdentity, decode_transfer_log, find_unreconciled

CHAIN_ID = 84532
TOKEN = "0x" + "44" * 20
_addr = st.integers(min_value=0, max_value=2**160 - 1).map(lambda n: "0x" + f"{n:040x}")
_uint = st.integers(min_value=0, max_value=2**256 - 1)
_hex32 = st.integers(min_value=0, max_value=2**256 - 1).map(lambda n: "0x" + f"{n:064x}")
_unique_payments = st.lists(st.tuples(_hex32, _uint, _addr), max_size=25, unique_by=lambda x: x[0])


@given(payer=_addr, value=_uint, tx=_hex32, index=st.integers(min_value=0, max_value=2**32))
def test_decode_is_faithful(payer: str, value: int, tx: str, index: int) -> None:
    log = transfer_log(token=TOKEN, value=value, tx_hash=tx, log_index=index, payer=payer)
    credit = decode_transfer_log(log, chain_id=CHAIN_ID)
    assert credit.value == value
    assert credit.payer == payer.lower()
    assert credit.payee == PAYEE
    assert credit.identity == SettlementIdentity(CHAIN_ID, TOKEN, tx, index)


@given(payments=_unique_payments, data=st.data())
def test_unreconciled_is_exact_identity_complement(
    payments: list[tuple[str, int, str]], data: st.DataObject
) -> None:
    logs = [
        transfer_log(token=TOKEN, value=value, tx_hash=tx, log_index=index, payer=payer)
        for index, (tx, value, payer) in enumerate(payments)
    ]
    identities = [
        SettlementIdentity(CHAIN_ID, TOKEN, tx, index)
        for index, (tx, _value, _payer) in enumerate(payments)
    ]
    known = set(
        data.draw(st.lists(st.sampled_from(identities), max_size=len(identities)))
        if identities
        else []
    )
    result = find_unreconciled(logs, known, chain_id=CHAIN_ID)
    assert [credit.identity for credit in result] == [
        identity for identity in identities if identity not in known
    ]


@given(payments=_unique_payments)
def test_recording_a_payment_never_grows_the_gap(
    payments: list[tuple[str, int, str]],
) -> None:
    logs = [
        transfer_log(token=TOKEN, value=value, tx_hash=tx, log_index=index, payer=payer)
        for index, (tx, value, payer) in enumerate(payments)
    ]
    base = {credit.identity for credit in find_unreconciled(logs, set(), chain_id=CHAIN_ID)}
    if not base:
        return
    recorded = next(iter(base))
    remaining = {
        credit.identity for credit in find_unreconciled(logs, {recorded}, chain_id=CHAIN_ID)
    }
    assert remaining == base - {recorded}
