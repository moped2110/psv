"""Differential testing, offline: one on-chain fact, several SUT beliefs.

Two systems watch the same settled payment. The correct one watches the token's
actual (legacy) Transfer event; the drifted one watches a different topic0 (the
SC1 blind spot). Against the *same* chain truth they must disagree — and the
differential harness names the faulty one.
"""

from __future__ import annotations

from psv.chain import SettlementTruth
from psv.differential import run_differential
from psv.divergence import DivergenceKind
from psv.reference_sut.confirmer import TOPIC_TRANSFER, EventWatchingConfirmer, topic_addr

PAYER = "0x" + "11" * 20
PAYEE = "0x" + "22" * 20
TOKEN = "0x" + "a0" * 20
DRIFTED_TOPIC0 = "0x" + "58" * 32  # a proxy-upgraded event signature the SUT never learned
AMOUNT = 10_000


def _log_source(emitted_topic0: str, value: int):
    """A fake eth_getLogs: returns the merchant-credit log only to a confirmer that
    asks for the topic0 the token actually emitted."""

    def fetch(addr: str, topics: list[str | None], from_block: int) -> list[dict[str, object]]:
        if topics and topics[0] == emitted_topic0:
            return [
                {
                    "data": hex(value),
                    "topics": [emitted_topic0, topic_addr(PAYER), topic_addr(PAYEE)],
                }
            ]
        return []

    return fetch


def _believes_paid(watched_topic0: str, emitted_topic0: str) -> bool:
    confirmer = EventWatchingConfirmer(
        fetch_logs=_log_source(emitted_topic0, AMOUNT), watched_topic0=watched_topic0
    )
    return confirmer.is_settled(token=TOKEN, payer=PAYER, payee=PAYEE, min_value=AMOUNT)


def _paid_truth() -> SettlementTruth:
    # The settlement really happened on-chain.
    return SettlementTruth(
        nonce_consumed=True,
        payer_balance_after=0,
        payee_balance_after=AMOUNT,
        payer_delta=-AMOUNT,
        payee_delta=AMOUNT,
    )


def test_differential_localises_the_drifted_sut() -> None:
    emitted = TOPIC_TRANSFER  # the token emits the legacy Transfer event
    beliefs = {
        "correct-sut": _believes_paid(TOPIC_TRANSFER, emitted),  # watches the right event
        "drifted-sut": _believes_paid(DRIFTED_TOPIC0, emitted),  # watches the wrong one → blind
    }
    assert beliefs == {"correct-sut": True, "drifted-sut": False}

    result = run_differential(_paid_truth(), beliefs, required_amount=AMOUNT)
    assert result.disagree is True
    assert result.has_finding is True
    # The correct SUT is consistent; the drifted one loses the payment silently.
    assert result.verdicts["correct-sut"].kind is DivergenceKind.CONSISTENT_PAID
    assert result.verdicts["drifted-sut"].kind is DivergenceKind.SILENT_LOSS
    assert set(result.failing) == {"drifted-sut"}


def test_differential_no_finding_when_both_correct_agree() -> None:
    emitted = TOPIC_TRANSFER
    beliefs = {
        "sut-a": _believes_paid(TOPIC_TRANSFER, emitted),
        "sut-b": _believes_paid(TOPIC_TRANSFER, emitted),
    }
    assert beliefs == {"sut-a": True, "sut-b": True}

    result = run_differential(_paid_truth(), beliefs, required_amount=AMOUNT)
    assert result.disagree is False
    assert result.has_finding is False
    assert all(v.kind is DivergenceKind.CONSISTENT_PAID for v in result.verdicts.values())
