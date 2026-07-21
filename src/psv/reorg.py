"""Reorg & finality helpers — the R-class system-level failure (Phase 2).

A settlement that looks final can be undone by a chain **reorganization**: blocks
are dropped and re-mined, and a transaction that was on-chain a moment ago is
suddenly gone. A payment system that treats the first inclusion as final will
then hold a *phantom credit* — it believes an order is paid while the chain shows
no funds moved and the EIP-3009 nonce free again.

The defense is **finality by confirmations**: don't treat a settlement as final
until it is buried under enough blocks that a reorg of that depth is implausible.

This module is split so the decision logic is offline-testable:
  * ``confirmations`` / ``is_final`` are pure arithmetic.
  * ``take_checkpoint`` / ``reorg_to`` drive Anvil's snapshot/revert to *simulate*
    a reorg deterministically (reverting drops every block mined after the
    checkpoint — exactly a reorg of that depth). Used by the on-chain tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .anvil import RpcClient

_HASH_RE = re.compile(r"0x[0-9a-fA-F]{64}")


def _block(value: int, what: str) -> int:
    """Validate an exact block number within the uint256 domain."""
    if type(value) is not int or not 0 <= value <= 2**256 - 1:
        raise ValueError(f"{what} must be a uint256")
    return value


def confirmations(current_block: int, tx_block: int) -> int:
    """Blocks confirming a tx, inclusive of its own block. 0 if not yet mined."""
    current = _block(current_block, "current_block")
    transaction = _block(tx_block, "tx_block")
    if transaction == 0 or current < transaction:
        return 0
    return current - transaction + 1


def is_final(current_block: int, tx_block: int, required_confirmations: int) -> bool:
    """Whether a settlement is deep enough to be treated as final."""
    if type(required_confirmations) is not int or required_confirmations <= 0:
        raise ValueError("required_confirmations must be a positive integer")
    return confirmations(current_block, tx_block) >= required_confirmations


def _hash(value: str, what: str) -> str:
    """Validate and normalize an exact 32-byte block hash."""
    if _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{what} must be an exact 32-byte hash")
    return value.lower()


@dataclass(frozen=True)
class SettlementFinality:
    """A reorg-aware finality verdict for one settlement: whether it may be treated
    as final, its current confirmation depth, whether its block was reorged out, and
    a human-readable reason. ``final`` is the only safe gate for crediting an order."""

    final: bool
    confirmations: int
    reorged: bool
    reason: str


def assess_finality(
    *,
    settlement_block_number: int,
    settlement_block_hash: str,
    current_block: int,
    canonical_block_hash: str | None,
    required_confirmations: int,
) -> SettlementFinality:
    """Judge whether a matched settlement is final under reorg-aware finality.

    ``is_final`` only measures depth; it cannot see that the block a settlement was mined
    in has been replaced. A reorg re-mines the block at the settlement's height, so if the
    ``canonical_block_hash`` now at that height no longer equals ``settlement_block_hash``,
    the original inclusion was undone and the settlement MUST NOT be treated as final —
    otherwise the system holds a phantom credit while the chain shows the nonce free again.

    ``canonical_block_hash`` is the hash the caller independently reads for
    ``settlement_block_number`` at the current head (``None`` if that height is not yet
    reached or the block is unavailable). Depth alone is never enough: a deep settlement on
    a reorged fork is still not final.
    """
    if type(required_confirmations) is not int or required_confirmations <= 0:
        raise ValueError("required_confirmations must be a positive integer")
    settlement_hash = _hash(settlement_block_hash, "settlement_block_hash")
    depth = confirmations(current_block, settlement_block_number)

    if canonical_block_hash is not None and _hash(canonical_block_hash, "canonical_block_hash") != (
        settlement_hash
    ):
        return SettlementFinality(
            final=False,
            confirmations=depth,
            reorged=True,
            reason="settlement block was reorged out (canonical hash differs); re-confirm inclusion",
        )
    if depth == 0:
        return SettlementFinality(
            final=False, confirmations=0, reorged=False, reason="settlement not yet mined"
        )
    if canonical_block_hash is None:
        return SettlementFinality(
            final=False,
            confirmations=depth,
            reorged=False,
            reason="canonical block hash unavailable at settlement height; cannot confirm canonicality",
        )
    if depth < required_confirmations:
        return SettlementFinality(
            final=False,
            confirmations=depth,
            reorged=False,
            reason=f"insufficient confirmations ({depth} < {required_confirmations})",
        )
    return SettlementFinality(
        final=True, confirmations=depth, reorged=False, reason="final: deep and canonical"
    )


def take_checkpoint(rpc: RpcClient) -> str:
    """Snapshot the chain so a later :func:`reorg_to` can drop the blocks after it."""
    return rpc.snapshot()


def reorg_to(rpc: RpcClient, checkpoint: str) -> bool:
    """Simulate a reorg: revert to ``checkpoint``, dropping every block since.

    Anvil's ``evm_revert`` restores state and removes the blocks mined after the
    snapshot — so any settlement included in those blocks is undone (balances and
    the EIP-3009 nonce return to their pre-settlement state), exactly as a real
    reorg of that depth would do.
    """
    return rpc.revert(checkpoint)
