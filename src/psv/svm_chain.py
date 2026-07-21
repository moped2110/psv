"""Independent SVM settlement truth — the Solana counterpart to the EVM oracle (PSV-RD-004).

psv verifies EVM settlement by reading the token's own state (nonce burned, balances) and
comparing it against the SUT's belief. On SVM there is no EIP-3009 nonce: an exact-scheme
payment is a partially-signed transaction whose replay protection is the recent blockhash and
signature uniqueness, and whose ground truth is the SPL token balance change recorded in the
transaction's own metadata. This module reads that metadata independently of any SUT claim and
produces a :class:`psv.chain.SettlementTruth`, so the existing divergence detectors
(:func:`psv.divergence.detect_payment_divergence` for exact, ``detect_metered_divergence`` for
upto) grade an SVM settlement unchanged — the system-level counterpart to the protocol-level
SVM verifier in the sibling conformance suite.

Every field of the transaction meta is untrusted input and is validated fail-closed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .chain import SettlementTruth
from .divergence import settlement_truth_from_balances

# CAIP-2 Solana networks (genesis-hash chain refs), mirrored from the x402 reference client.
SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
SOLANA_TESTNET = "solana:4uhcVJyU9pJkvQyS88uRDiswHXSCkY3z"
SOLANA_NETWORKS = frozenset({SOLANA_MAINNET, SOLANA_DEVNET, SOLANA_TESTNET})

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
_TOKEN_PROGRAMS = frozenset({TOKEN_PROGRAM, TOKEN_2022_PROGRAM})

# base58 (Bitcoin alphabet); a Solana public key is 32–44 base58 characters.
_BASE58_RE = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")
_U64_MAX = 2**64 - 1


class SvmEvidenceError(ValueError):
    """A malformed or internally inconsistent SVM transaction meta object."""


def _b58(value: object, what: str) -> str:
    """Validate and return a base58 Solana address."""
    if not isinstance(value, str) or _BASE58_RE.fullmatch(value) is None:
        raise SvmEvidenceError(f"{what} must be a base58 Solana address")
    return value


def _amount(value: object, what: str) -> int:
    """Decode an SPL ``uiTokenAmount.amount`` (a decimal uint64 string) to an int."""
    if not isinstance(value, str) or not value.isdigit():
        raise SvmEvidenceError(f"{what} must be a decimal uint64 string")
    result = int(value)
    if not 0 <= result <= _U64_MAX:
        raise SvmEvidenceError(f"{what} is outside uint64")
    return result


def _balance_for(balances: list[object], *, owner: str, mint: str, what: str) -> int:
    """Return the ``owner``+``mint`` token balance in ``balances``, or 0 if that account
    is absent (e.g. an ATA created within the transaction has no pre-balance entry)."""
    found: list[int] = []
    for entry in balances:
        if not isinstance(entry, dict):
            raise SvmEvidenceError(f"{what} entry must be an object")
        if entry.get("owner") == owner and entry.get("mint") == mint:
            ui = entry.get("uiTokenAmount")
            if not isinstance(ui, dict):
                raise SvmEvidenceError(f"{what} uiTokenAmount must be an object")
            found.append(_amount(ui.get("amount"), f"{what} amount"))
    if len(found) > 1:
        raise SvmEvidenceError(f"{what}: more than one token account for owner+mint")
    return found[0] if found else 0


def settlement_truth_from_svm_meta(
    meta: dict[str, object],
    *,
    mint: str,
    payer_owner: str,
    payee_owner: str,
) -> SettlementTruth:
    """Derive :class:`SettlementTruth` from a Solana ``getTransaction`` ``meta`` object.

    ``meta.err`` is ``None`` on success (the transaction is final and its authorization is
    consumed); any other value means the transaction failed and moved nothing. The payer's
    and payee's SPL balances for ``mint`` are read from ``pre``/``postTokenBalances`` and the
    deltas become the ground truth. Because there is no on-chain nonce on SVM, ``err is None``
    is what feeds ``nonce_consumed`` — a failed or non-settling transaction that the SUT still
    credits surfaces as ``PHANTOM_CREDIT`` through the shared detector.
    """
    if not isinstance(meta, dict):
        raise SvmEvidenceError("meta must be an object")
    mint = _b58(mint, "mint")
    payer_owner = _b58(payer_owner, "payer_owner")
    payee_owner = _b58(payee_owner, "payee_owner")
    if payer_owner == payee_owner:
        raise SvmEvidenceError("payer and payee owners must differ")
    if "err" not in meta:
        raise SvmEvidenceError("meta must carry an err field")
    pre = meta.get("preTokenBalances")
    post = meta.get("postTokenBalances")
    if not isinstance(pre, list) or not isinstance(post, list):
        raise SvmEvidenceError("meta must carry pre/postTokenBalances lists")
    succeeded = meta["err"] is None
    return settlement_truth_from_balances(
        nonce_consumed=succeeded,
        payer_before=_balance_for(pre, owner=payer_owner, mint=mint, what="pre payer"),
        payer_after=_balance_for(post, owner=payer_owner, mint=mint, what="post payer"),
        payee_before=_balance_for(pre, owner=payee_owner, mint=mint, what="pre payee"),
        payee_after=_balance_for(post, owner=payee_owner, mint=mint, what="post payee"),
    )


@dataclass(frozen=True)
class SvmRailAttestation:
    """Reviewed metadata for an SVM reconciliation rail, mirroring the EVM attestation's
    fail-closed spirit with SVM-shaped fields (no EVM proxy/code identity)."""

    version: str
    reviewed_on: date
    authoritative_sources: tuple[str, ...]
    network_class: str
    expected_decimals: int
    calibrated: bool = False

    def __post_init__(self) -> None:
        """Validate the attestation's version, network class and decimals."""
        if not self.version or not self.authoritative_sources:
            raise ValueError("svm rail attestation needs a version and authoritative source")
        if self.network_class not in {"local", "testnet", "mainnet"}:
            raise ValueError("invalid network classification")
        if not 0 <= self.expected_decimals <= 36:
            raise ValueError("attested decimals must be within [0, 36]")


@dataclass(frozen=True)
class SvmRailConfig:
    """Versioned SVM token identity (network, mint, program) for one reconciliation rail.
    Read-only by construction: signing is permanently disabled, as on every psv rail."""

    key: str
    label: str
    network: str
    mint: str
    decimals: int
    token_program: str
    attestation: SvmRailAttestation
    signing_enabled: bool = False

    def __post_init__(self) -> None:
        """Validate the SVM network, mint, token program and read-only invariant."""
        if self.network not in SOLANA_NETWORKS:
            raise ValueError("svm rail network must be a known solana CAIP-2 id")
        if _BASE58_RE.fullmatch(self.mint) is None:
            raise ValueError("svm rail mint must be a base58 Solana address")
        if self.token_program not in _TOKEN_PROGRAMS:
            raise ValueError("svm rail token_program must be SPL Token or Token-2022")
        if type(self.decimals) is not int or not 0 <= self.decimals <= 36:
            raise ValueError("svm rail decimals must be within [0, 36]")
        if self.signing_enabled:
            raise ValueError("signing is disabled for every reconciliation rail")
        if self.attestation.expected_decimals != self.decimals:
            raise ValueError("svm rail decimals differ from the reviewed attestation")


_CIRCLE_USDC = "https://developers.circle.com/stablecoins/usdc-contract-addresses"

SVM_RAILS: dict[str, SvmRailConfig] = {
    "usdc-solana-devnet": SvmRailConfig(
        key="usdc-solana-devnet",
        label="USDC on Solana devnet",
        network=SOLANA_DEVNET,
        mint="4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU",
        decimals=6,
        token_program=TOKEN_PROGRAM,
        attestation=SvmRailAttestation(
            version="2026-07-19",
            reviewed_on=date(2026, 7, 19),
            authoritative_sources=(_CIRCLE_USDC,),
            network_class="testnet",
            expected_decimals=6,
        ),
    ),
}


def get_svm_rail(key: str) -> SvmRailConfig:
    """Return the reviewed SVM rail for ``key`` or raise ``KeyError`` if unknown."""
    try:
        return SVM_RAILS[key]
    except KeyError:
        raise KeyError(f"unknown svm rail: {key}") from None
