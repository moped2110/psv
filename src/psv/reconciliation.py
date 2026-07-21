"""Strict settlement identities and ledger reconciliation.

One transaction can contain several token transfers.  A transaction hash is
therefore not a settlement identifier.  The stable identity used here is the
tuple ``(chain_id, asset, transaction_hash, log_index)``.  Every decoded credit
also retains the block hash and ``removed`` flag needed to detect reorged logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

# Transfer(address indexed from, address indexed to, uint256 value) topic0.
TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_UINT256_MAX = 2**256 - 1


class ReconciliationError(ValueError):
    """A malformed or internally inconsistent settlement evidence set."""


def _exact_hex(value: object, *, size: int, what: str) -> str:
    """Validate, normalize, and return fixed-size 0x-prefixed hex."""
    if not isinstance(value, str) or not value.startswith("0x") or len(value) != 2 + size * 2:
        raise ReconciliationError(f"{what} must be 0x-prefixed {size}-byte hex")
    try:
        bytes.fromhex(value[2:])
    except ValueError as exc:
        raise ReconciliationError(f"{what} must be 0x-prefixed {size}-byte hex") from exc
    return value.lower()


def _quantity(value: object, *, what: str) -> int:
    """Decode a canonical JSON-RPC quantity constrained to uint256."""
    if (
        not isinstance(value, str)
        or not value.startswith("0x")
        or value == "0x"
        or (len(value) > 3 and value[2] == "0")
    ):
        raise ReconciliationError(f"{what} must be a canonical JSON-RPC hex quantity")
    try:
        result = int(value[2:], 16)
    except ValueError as exc:
        raise ReconciliationError(f"{what} must be a canonical JSON-RPC hex quantity") from exc
    if not 0 <= result <= _UINT256_MAX:
        raise ReconciliationError(f"{what} is outside uint256")
    return result


def _address(value: object, *, what: str) -> str:
    """Validate and normalize an exact 20-byte address."""
    return _exact_hex(value, size=20, what=what)


def topic_addr(addr: str) -> str:
    """Encode an exact EVM address as a 32-byte indexed topic."""
    exact = _address(addr, what="address")
    return "0x" + "0" * 24 + exact[2:]


def _addr_from_topic(topic: object, *, what: str) -> str:
    """Decode a canonically padded address from an indexed topic."""
    exact = _exact_hex(topic, size=32, what=what)
    if exact[2:26] != "0" * 24:
        raise ReconciliationError(f"{what} is not a canonically padded address topic")
    return "0x" + exact[-40:]


@dataclass(frozen=True, order=True)
class SettlementIdentity:
    """Collision-free identity of one emitted settlement credit."""

    chain_id: int
    asset: str
    tx_hash: str
    log_index: int

    def __post_init__(self) -> None:
        """Normalize and validate all components of the settlement identity."""
        if self.chain_id <= 0:
            raise ReconciliationError("chain_id must be positive")
        object.__setattr__(self, "asset", _address(self.asset, what="asset"))
        object.__setattr__(self, "tx_hash", _exact_hex(self.tx_hash, size=32, what="tx hash"))
        if not 0 <= self.log_index <= _UINT256_MAX:
            raise ReconciliationError("log_index is outside uint256")


@dataclass(frozen=True)
class OnChainCredit:
    """A strictly decoded token credit and its reorg-relevant provenance."""

    identity: SettlementIdentity
    block_hash: str
    block_number: int
    payer: str
    payee: str
    value: int
    removed: bool

    def __post_init__(self) -> None:
        """Normalize and validate complete credit and reorg provenance."""
        object.__setattr__(
            self, "block_hash", _exact_hex(self.block_hash, size=32, what="block hash")
        )
        object.__setattr__(self, "payer", _address(self.payer, what="payer"))
        object.__setattr__(self, "payee", _address(self.payee, what="payee"))
        if not 0 <= self.block_number <= _UINT256_MAX:
            raise ReconciliationError("block_number is outside uint256")
        if not 0 <= self.value <= _UINT256_MAX:
            raise ReconciliationError("value is outside uint256")
        if not isinstance(self.removed, bool):
            raise ReconciliationError("removed must be a boolean")

    @property
    def chain_id(self) -> int:
        """Return the chain component of the settlement identity."""
        return self.identity.chain_id

    @property
    def asset(self) -> str:
        """Return the normalized token component of the settlement identity."""
        return self.identity.asset

    @property
    def tx_hash(self) -> str:
        """Return the transaction-hash component of the settlement identity."""
        return self.identity.tx_hash

    @property
    def log_index(self) -> int:
        """Return the log-index component of the settlement identity."""
        return self.identity.log_index

    @property
    def payer_norm(self) -> str:
        """Return the already normalized payer address."""
        return self.payer


def decode_transfer_log(log: dict[str, object], *, chain_id: int) -> OnChainCredit:
    """Decode a complete JSON-RPC ``Transfer`` log without coercion or defaults."""
    if chain_id <= 0:
        raise ReconciliationError("chain_id must be positive")
    topics = log.get("topics")
    if not isinstance(topics, list) or len(topics) != 3:
        raise ReconciliationError("Transfer log must contain exactly three topics")
    if _exact_hex(topics[0], size=32, what="topic0") != TOPIC_TRANSFER:
        raise ReconciliationError("log topic0 is not Transfer(address,address,uint256)")

    data = _exact_hex(log.get("data"), size=32, what="Transfer data")
    removed = log.get("removed")
    if not isinstance(removed, bool):
        raise ReconciliationError("removed must be a boolean")

    asset = _address(log.get("address"), what="log address")
    tx_hash = _exact_hex(log.get("transactionHash"), size=32, what="transaction hash")
    log_index = _quantity(log.get("logIndex"), what="log index")
    block_hash = _exact_hex(log.get("blockHash"), size=32, what="block hash")
    block_number = _quantity(log.get("blockNumber"), what="block number")

    return OnChainCredit(
        identity=SettlementIdentity(chain_id, asset, tx_hash, log_index),
        block_hash=block_hash,
        block_number=block_number,
        payer=_addr_from_topic(topics[1], what="payer topic"),
        payee=_addr_from_topic(topics[2], what="payee topic"),
        value=int(data, 16),
        removed=removed,
    )


LedgerEntry: TypeAlias = SettlementIdentity | OnChainCredit


def find_unreconciled(
    transfers: list[dict[str, object]],
    ledger_entries: set[LedgerEntry],
    *,
    chain_id: int,
    expected_asset: str | None = None,
    expected_payee: str | None = None,
) -> list[OnChainCredit]:
    """Return unique, canonical credits missing from the exact-identity ledger.

    Removed logs never count as credits.  Conflicting observations of the same
    identity (including a removed/non-removed pair) make the snapshot
    inconclusive and raise :class:`ReconciliationError` rather than returning a
    misleading reconciliation result.
    """
    known = {
        entry.identity if isinstance(entry, OnChainCredit) else entry for entry in ledger_entries
    }
    asset = _address(expected_asset, what="expected asset") if expected_asset else None
    payee = _address(expected_payee, what="expected payee") if expected_payee else None

    observations: dict[SettlementIdentity, OnChainCredit] = {}
    for log in transfers:
        credit = decode_transfer_log(log, chain_id=chain_id)
        if asset is not None and credit.asset != asset:
            raise ReconciliationError(f"cross-asset log: expected {asset}, observed {credit.asset}")
        if payee is not None and credit.payee != payee:
            raise ReconciliationError(
                f"cross-recipient log: expected {payee}, observed {credit.payee}"
            )
        previous = observations.get(credit.identity)
        if previous is not None and previous != credit:
            raise ReconciliationError(
                f"conflicting observations for settlement {credit.identity!r}"
            )
        observations[credit.identity] = credit

    return sorted(
        (
            credit
            for identity, credit in observations.items()
            if not credit.removed and identity not in known
        ),
        key=lambda credit: (
            credit.block_number,
            credit.log_index,
            credit.tx_hash,
            credit.asset,
        ),
    )


def reconcile_asset_scoped(
    transfers: list[dict[str, object]],
    ledger_entries: set[LedgerEntry],
    *,
    chain_id: int,
    expected_asset: str,
    expected_payee: str | None = None,
) -> list[OnChainCredit]:
    """Reconcile only ``expected_asset`` credits from a possibly multi-asset snapshot.

    :func:`find_unreconciled` fails closed on any cross-asset log — safe, but it cannot
    process a realistic block that also carries unrelated tokens. This scopes such a
    snapshot to the expected asset first, so a same-block transfer of a *different* asset to
    the same payee is excluded rather than mis-attributed as this order's payment: the
    multi-asset settlement race (PSV-RD-003). Cross-recipient logs within the scoped asset
    still fail closed, and every log is decoded (and validated) exactly as in the strict path.
    """
    asset = _address(expected_asset, what="expected asset")
    scoped = [
        log for log in transfers if decode_transfer_log(log, chain_id=chain_id).asset == asset
    ]
    return find_unreconciled(
        scoped,
        ledger_entries,
        chain_id=chain_id,
        expected_asset=asset,
        expected_payee=expected_payee,
    )
