"""Offline unit tests for the event-watching confirmer + its SC1 blindness.

We inject a fake log fetcher that simulates the token before and after the event
drift, and prove the confirmer confirms a legacy Transfer but goes blind once the
settlement event's topic0 changes — with no chain involved.
"""

from __future__ import annotations

from psv.chain import TOPIC_TRANSFER, TOPIC_TRANSFER_V2
from psv.reference_sut.confirmer import EventWatchingConfirmer, topic_addr

TOKEN = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
PAYER = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
PAYEE = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"


def _legacy_log(value: int) -> dict[str, object]:
    return {
        "address": TOKEN,
        "topics": [TOPIC_TRANSFER, topic_addr(PAYER), topic_addr(PAYEE)],
        "data": hex(value),
    }


def make_fetcher(emitted_topic0: str, value: int):
    """A fake chain that emits ONE settlement log under ``emitted_topic0``."""

    def fetch(address: str, topics: list[str | None], from_block: int) -> list[dict[str, object]]:
        # eth_getLogs only returns logs whose topic0 matches the requested filter.
        if topics and topics[0] == emitted_topic0:
            return [
                {
                    "address": address,
                    "topics": [emitted_topic0, topic_addr(PAYER), topic_addr(PAYEE)],
                    "data": hex(value),
                }
            ]
        return []

    return fetch


def test_confirms_legacy_transfer() -> None:
    c = EventWatchingConfirmer(fetch_logs=make_fetcher(TOPIC_TRANSFER, 10_000))
    assert c.is_settled(token=TOKEN, payer=PAYER, payee=PAYEE, min_value=10_000)


def test_blind_after_event_drift() -> None:
    # Token now emits TransferV2 (different topic0); the confirmer still watches legacy.
    c = EventWatchingConfirmer(fetch_logs=make_fetcher(TOPIC_TRANSFER_V2, 10_000))
    assert not c.is_settled(token=TOKEN, payer=PAYER, payee=PAYEE, min_value=10_000)


def test_underpayment_not_confirmed() -> None:
    c = EventWatchingConfirmer(fetch_logs=lambda a, t, b: [_legacy_log(9_999)])
    assert not c.is_settled(token=TOKEN, payer=PAYER, payee=PAYEE, min_value=10_000)


def test_exact_amount_confirmed() -> None:
    c = EventWatchingConfirmer(fetch_logs=lambda a, t, b: [_legacy_log(10_000)])
    assert c.is_settled(token=TOKEN, payer=PAYER, payee=PAYEE, min_value=10_000)
