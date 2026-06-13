"""Settlement confirmation by event-watching — the SC1-vulnerable component.

A payment system must decide *when a payment is settled*. A very common pattern
is to scan the chain for the token's transfer event to the merchant address.
This module implements exactly that, and is deliberately the weak point: it is
pinned to ONE event signature (``topic0``). If the token's settlement event
drifts — e.g. a proxy upgrade changes ``Transfer`` to a new signature — the
filter matches nothing and every real payment looks unsettled.

The log-fetching transport is injected (``LogFetcher``), so the brittle decision
logic is fully unit-testable offline: feed it logs as if from a drifted token and
watch it go blind, with no chain.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# Legacy Transfer(address,address,uint256) topic0. The confirmer watches THIS.
TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# A log fetcher: (address, topics, from_block) -> list of log objects.
LogFetcher = Callable[[str, list[str | None], int], list[dict[str, object]]]


def topic_addr(addr: str) -> str:
    return "0x" + addr.lower().removeprefix("0x").rjust(64, "0")


@dataclass
class EventWatchingConfirmer:
    """Confirms settlement iff a matching legacy ``Transfer`` log is found.

    ``watched_topic0`` defaults to the legacy Transfer signature. The SUT never
    changes it — that immutability is precisely what SC1 exploits.
    """

    fetch_logs: LogFetcher
    watched_topic0: str = TOPIC_TRANSFER

    def is_settled(
        self, *, token: str, payer: str, payee: str, min_value: int, from_block: int = 0
    ) -> bool:
        topics: list[str | None] = [self.watched_topic0, topic_addr(payer), topic_addr(payee)]
        logs = self.fetch_logs(token, topics, from_block)
        for log in logs:
            raw = log.get("data", "0x")
            data = raw if isinstance(raw, str) else "0x"
            try:
                value = int(data, 16)
            except ValueError:
                continue
            if value >= min_value:
                return True
        return False
