"""Unit tests for reorg-aware finality — the reorg-safe confirmer decision (PSV-RD-001).

``is_final`` only measures depth. ``assess_finality`` adds canonicality: a settlement is final
only when it is both deep enough AND its block is still the canonical block at that height. A
reorg replaces that block, so a deep settlement on a reorged fork is a phantom credit, not a
final payment. Pure decision logic, tested offline; the Anvil snapshot/revert simulation lives
in the on-chain tests.
"""

from __future__ import annotations

import pytest

from psv.reorg import assess_finality

_HASH_A = "0x" + "ab" * 32
_HASH_B = "0x" + "cd" * 32


def test_final_when_deep_and_block_still_canonical() -> None:
    """Enough confirmations and the settlement block still canonical → final."""
    v = assess_finality(
        settlement_block_number=100,
        settlement_block_hash=_HASH_A,
        current_block=105,
        canonical_block_hash=_HASH_A,
        required_confirmations=3,
    )
    assert v.final
    assert not v.reorged
    assert v.confirmations == 6


def test_reorged_out_block_is_never_final_even_when_deep() -> None:
    """The canonical hash at the settlement height differs → reorged, not final."""
    v = assess_finality(
        settlement_block_number=100,
        settlement_block_hash=_HASH_A,
        current_block=200,
        canonical_block_hash=_HASH_B,
        required_confirmations=3,
    )
    assert not v.final
    assert v.reorged
    assert "reorged" in v.reason


def test_insufficient_confirmations_is_not_final() -> None:
    """Canonical but too shallow → not final, not reorged."""
    v = assess_finality(
        settlement_block_number=100,
        settlement_block_hash=_HASH_A,
        current_block=101,
        canonical_block_hash=_HASH_A,
        required_confirmations=5,
    )
    assert not v.final
    assert not v.reorged
    assert v.confirmations == 2


def test_unavailable_canonical_hash_blocks_finality() -> None:
    """Deep enough but the canonical hash is unavailable → cannot confirm canonicality."""
    v = assess_finality(
        settlement_block_number=100,
        settlement_block_hash=_HASH_A,
        current_block=105,
        canonical_block_hash=None,
        required_confirmations=3,
    )
    assert not v.final
    assert not v.reorged
    assert "canonical" in v.reason


def test_not_yet_mined_is_not_final() -> None:
    """A settlement whose block is ahead of the head has zero confirmations."""
    v = assess_finality(
        settlement_block_number=100,
        settlement_block_hash=_HASH_A,
        current_block=99,
        canonical_block_hash=None,
        required_confirmations=3,
    )
    assert not v.final
    assert v.confirmations == 0
    assert "not yet mined" in v.reason


def test_malformed_settlement_hash_is_rejected() -> None:
    """A non-32-byte settlement block hash is rejected."""
    with pytest.raises(ValueError, match="32-byte hash"):
        assess_finality(
            settlement_block_number=100,
            settlement_block_hash="0xdeadbeef",
            current_block=105,
            canonical_block_hash=_HASH_A,
            required_confirmations=3,
        )


def test_non_positive_confirmation_requirement_is_rejected() -> None:
    """A required confirmation depth must be a positive integer."""
    with pytest.raises(ValueError, match="positive integer"):
        assess_finality(
            settlement_block_number=100,
            settlement_block_hash=_HASH_A,
            current_block=105,
            canonical_block_hash=_HASH_A,
            required_confirmations=0,
        )
