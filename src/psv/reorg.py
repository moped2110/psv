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

from .anvil import RpcClient


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
