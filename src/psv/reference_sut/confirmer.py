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

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

# Legacy Transfer(address,address,uint256) topic0. The confirmer watches THIS.
TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TOPIC_AUTHORIZATION_USED = "0x98de503528ee59b575ef0c0a2576a82497bfc029a5685b209e9ec333479b10a5"

# A log fetcher: (address, topics, from_block) -> list of log objects.
LogFetcher = Callable[[str, list[str | None], int], list[dict[str, object]]]


def topic_addr(addr: str) -> str:
    """Encode an address as a padded indexed event topic."""
    return "0x" + addr.lower().removeprefix("0x").rjust(64, "0")


def topic_nonce(nonce: str) -> str:
    """Normalize an authorization nonce as a padded event topic."""
    return "0x" + nonce.lower().removeprefix("0x").rjust(64, "0")


def _same_hex(left: object, right: str) -> bool:
    """Compare two hexadecimal strings case-insensitively and without coercion."""
    return isinstance(left, str) and left.lower() == right.lower()


def _quantity(value: object) -> int | None:
    """Best-effort decode an integer or hexadecimal quantity for confirmer input."""
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    try:
        return int(value, 16)
    except ValueError:
        return None


def _topics(log: Mapping[str, object]) -> Sequence[object]:
    """Return a log's topic list or an empty immutable sequence."""
    raw = log.get("topics")
    return raw if isinstance(raw, list) else ()


def _receipt_log_matches_fetched(
    receipt_log: Mapping[str, object], fetched_logs: Sequence[Mapping[str, object]]
) -> bool:
    """Match a receipt log to independently fetched evidence by tx and index."""
    tx_hash = receipt_log.get("transactionHash")
    log_index = _quantity(receipt_log.get("logIndex"))
    if not isinstance(tx_hash, str) or log_index is None:
        return False
    return any(
        _same_hex(candidate.get("transactionHash"), tx_hash)
        and _quantity(candidate.get("logIndex")) == log_index
        for candidate in fetched_logs
    )


@dataclass
class EventWatchingConfirmer:
    """Confirms settlement iff a matching legacy ``Transfer`` log is found.

    ``watched_topic0`` defaults to the legacy Transfer signature. The SUT never
    changes it — that immutability is precisely what SC1 exploits.
    """

    fetch_logs: LogFetcher
    watched_topic0: str = TOPIC_TRANSFER

    def settlement_log_index(
        self,
        *,
        token: str,
        payer: str,
        payee: str,
        expected_value: int,
        authorization_nonce: str,
        submitted_tx: str,
        receipt: Mapping[str, object] | None,
        from_block: int = 0,
    ) -> int | None:
        """Return the exact confirmed Transfer log index, else ``None``.

        An unrelated historical transfer can never satisfy this decision: both
        required events must belong to ``submitted_tx`` and to distinct receipt
        log indices, and ``AuthorizationUsed`` must carry this order's nonce.
        """

        if receipt is None or _quantity(receipt.get("status")) != 1:
            return None
        if not _same_hex(receipt.get("transactionHash"), submitted_tx):
            return None
        if not _same_hex(receipt.get("to"), token):
            return None
        block_number = _quantity(receipt.get("blockNumber"))
        if block_number is None or block_number < from_block:
            return None

        topics: list[str | None] = [self.watched_topic0, topic_addr(payer), topic_addr(payee)]
        fetched_logs = self.fetch_logs(token, topics, block_number)
        raw_receipt_logs = receipt.get("logs")
        if not isinstance(raw_receipt_logs, list):
            return None
        receipt_logs = [log for log in raw_receipt_logs if isinstance(log, Mapping)]

        transfer_index: int | None = None
        authorization_index: int | None = None
        for log in receipt_logs:
            if log.get("removed") is True:
                continue
            if not _same_hex(log.get("address"), token):
                continue
            if not _same_hex(log.get("transactionHash"), submitted_tx):
                continue
            if _quantity(log.get("blockNumber")) != block_number:
                continue
            log_index = _quantity(log.get("logIndex"))
            if log_index is None:
                continue

            log_topics = _topics(log)
            if (
                len(log_topics) == 3
                and _same_hex(log_topics[0], self.watched_topic0)
                and _same_hex(log_topics[1], topic_addr(payer))
                and _same_hex(log_topics[2], topic_addr(payee))
                and _quantity(log.get("data")) == expected_value
                and _receipt_log_matches_fetched(log, fetched_logs)
            ):
                transfer_index = log_index
            if (
                len(log_topics) == 3
                and _same_hex(log_topics[0], TOPIC_AUTHORIZATION_USED)
                and _same_hex(log_topics[1], topic_addr(payer))
                and _same_hex(log_topics[2], topic_nonce(authorization_nonce))
            ):
                authorization_index = log_index

        if (
            transfer_index is not None
            and authorization_index is not None
            and transfer_index != authorization_index
        ):
            return transfer_index
        return None

    def is_settled(
        self,
        *,
        token: str,
        payer: str,
        payee: str,
        expected_value: int,
        authorization_nonce: str,
        submitted_tx: str,
        receipt: Mapping[str, object] | None,
        from_block: int = 0,
    ) -> bool:
        """Verify one exact submitted authorization from its mined receipt."""
        return (
            self.settlement_log_index(
                token=token,
                payer=payer,
                payee=payee,
                expected_value=expected_value,
                authorization_nonce=authorization_nonce,
                submitted_tx=submitted_tx,
                receipt=receipt,
                from_block=from_block,
            )
            is not None
        )
