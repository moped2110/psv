"""Reconciliation: match on-chain settlements against the system's own ledger.

D3 scenario — backup/restore vs. chain divergence. A payment system records each
settled payment in an internal ledger. If that ledger is rolled back (crash +
restore from an older backup, a botched migration, a dropped write) while the
chain has moved on, the system permanently *forgets* payments that really
happened on-chain. Without a reconciliation job, those payments are lost in
silence: the customer was debited, the merchant credited, and the system believes
the order was never paid.

Reconciliation is the defense: periodically enumerate on-chain settlements to the
merchant and check each one is represented in the ledger. Anything on-chain with
no ledger record is an **unreconciled credit** — money received that the system
cannot account for.

This module is pure and transport-agnostic: it decodes raw ``eth_getLogs`` output
and diffs it against the ledger's known settlement tx hashes, so the logic is
unit-tested offline with synthetic logs. The SUT supplies real logs.
"""

from __future__ import annotations

from dataclasses import dataclass

# Transfer(address indexed from, address indexed to, uint256 value) topic0.
TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def topic_addr(addr: str) -> str:
    """A 20-byte address as a 32-byte topic (0x + 64 hex)."""
    return "0x" + addr.lower().removeprefix("0x").rjust(64, "0")


def _addr_from_topic(topic: str) -> str:
    return "0x" + topic.lower().removeprefix("0x")[-40:]


@dataclass(frozen=True)
class OnChainCredit:
    """A settlement observed on-chain: funds ``value`` from ``payer`` in ``tx_hash``.

    ``asset`` is the emitting token contract (the log's ``address``). It lets a
    multi-asset merchant attribute each credit to the right token — two payments
    of equal value in different assets are distinct records, and reconciliation
    of one asset never marks another asset's credit as accounted for.
    """

    payer: str
    value: int
    tx_hash: str
    asset: str = ""

    @property
    def payer_norm(self) -> str:
        return self.payer.lower()


def decode_transfer_log(log: dict[str, object]) -> OnChainCredit:
    """Decode a raw ``Transfer`` log into an :class:`OnChainCredit`."""
    topics = log.get("topics")
    if not isinstance(topics, list) or len(topics) < 3:
        raise ValueError(f"not a Transfer log (topics={topics!r})")
    raw = log.get("data", "0x")
    data = raw if isinstance(raw, str) else "0x"
    tx = log.get("transactionHash", "")
    asset = log.get("address", "")
    return OnChainCredit(
        payer=_addr_from_topic(str(topics[1])),
        value=int(data, 16) if data not in ("", "0x") else 0,
        tx_hash=str(tx).lower(),
        asset=str(asset).lower(),
    )


def find_unreconciled(
    transfers: list[dict[str, object]], ledger_tx_hashes: set[str]
) -> list[OnChainCredit]:
    """On-chain credits to the merchant that the ledger has no record of.

    ``transfers`` are raw ``Transfer``-to-merchant logs; ``ledger_tx_hashes`` is
    the set of settlement tx hashes the system believes it processed. The diff is
    the silent-loss surface: real money in, no internal record.
    """
    known = {h.lower() for h in ledger_tx_hashes}
    out: list[OnChainCredit] = []
    for log in transfers:
        credit = decode_transfer_log(log)
        if credit.tx_hash not in known:
            out.append(credit)
    return out
