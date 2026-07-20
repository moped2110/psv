"""Attested, read-only EIP-3009 rail reconciliation.

The CLI never signs or submits transactions.  A verdict is derived from one
canonical receipt and one pinned block snapshot, not from several ``latest``
reads or caller-provided balance deltas.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date

from .anvil import RpcClient, RpcError
from .chain import TOPIC_AUTHORIZATION_USED, SettlementTruth, TokenView
from .divergence import Divergence, DivergenceKind, Severity, detect_payment_divergence
from .reconciliation import (
    TOPIC_TRANSFER,
    OnChainCredit,
    ReconciliationError,
    decode_transfer_log,
    topic_addr,
)

_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_BYTES32_RE = _HASH_RE
_UINT256_MAX = 2**256 - 1


class ChainEvidenceError(RpcError):
    """Chain responses are valid individually but cannot prove one settlement."""


@dataclass(frozen=True)
class FinalityPolicy:
    """The explicit block selection and depth required for a rail verdict."""

    block_tag: str
    minimum_confirmations: int

    def __post_init__(self) -> None:
        """Validate the supported block tag and positive confirmation depth."""
        if self.block_tag not in {"latest", "safe", "finalized"}:
            raise ValueError("unsupported finality block tag")
        if type(self.minimum_confirmations) is not int or self.minimum_confirmations < 1:
            raise ValueError("minimum_confirmations must be positive")


@dataclass(frozen=True)
class RailAttestation:
    """Reviewed metadata used to fail closed when a live rail drifts."""

    version: str
    reviewed_on: date
    authoritative_sources: tuple[str, ...]
    interface: str
    network_class: str
    proxy_kind: str
    expected_decimals: int
    domain_name: str | None
    domain_version: str | None
    reviewed_block_number: int | None = None
    reviewed_block_hash: str | None = None
    implementation_address: str | None = None
    expected_code_sha256: str | None = None
    proxy_implementation_slot: str | None = None
    implementation_code_sha256: str | None = None
    calibrated: bool = False

    def __post_init__(self) -> None:
        """Validate reviewed metadata and require full identity for calibrated rails."""
        if not self.version or not self.authoritative_sources:
            raise ValueError("rail attestation needs a version and authoritative source")
        if self.interface != "eip3009":
            raise ValueError("only the attested eip3009 interface is supported")
        if self.network_class not in {"local", "testnet", "mainnet"}:
            raise ValueError("invalid network classification")
        if not 0 <= self.expected_decimals <= 36:
            raise ValueError("attested decimals must be within [0, 36]")
        if (self.reviewed_block_number is None) != (self.reviewed_block_hash is None):
            raise ValueError("reviewed block number/hash must be set together")
        if self.reviewed_block_number is not None and self.reviewed_block_number < 0:
            raise ValueError("reviewed block number cannot be negative")
        if (
            self.reviewed_block_hash is not None
            and _HASH_RE.fullmatch(self.reviewed_block_hash) is None
        ):
            raise ValueError("reviewed_block_hash must be an exact hash")
        if (
            self.expected_code_sha256 is not None
            and re.fullmatch(r"[0-9a-f]{64}", self.expected_code_sha256) is None
        ):
            raise ValueError("expected_code_sha256 must be lowercase SHA-256 hex")
        if (
            self.implementation_address is not None
            and re.fullmatch(r"0x[0-9a-fA-F]{40}", self.implementation_address) is None
        ):
            raise ValueError("implementation_address must be an exact EVM address")
        if (
            self.proxy_implementation_slot is not None
            and _HASH_RE.fullmatch(self.proxy_implementation_slot) is None
        ):
            raise ValueError("proxy_implementation_slot must be an exact bytes32 slot")
        if (
            self.implementation_code_sha256 is not None
            and re.fullmatch(r"[0-9a-f]{64}", self.implementation_code_sha256) is None
        ):
            raise ValueError("implementation_code_sha256 must be lowercase SHA-256 hex")
        if self.network_class == "mainnet" and self.calibrated:
            if (
                self.reviewed_block_number is None
                or self.expected_code_sha256 is None
                or self.proxy_kind != "none"
                and (
                    self.implementation_address is None
                    or self.proxy_implementation_slot is None
                    or self.implementation_code_sha256 is None
                )
            ):
                raise ValueError("calibrated mainnet rails require pinned code/proxy identity")


@dataclass(frozen=True)
class RailConfig:
    """Versioned token identity, interface and finality metadata for one rail."""

    key: str
    label: str
    chain_id: int
    token_address: str
    decimals: int
    token_name: str | None
    token_version: str | None
    finality: FinalityPolicy
    attestation: RailAttestation
    signing_enabled: bool = False

    def __post_init__(self) -> None:
        """Validate token identity and enforce permanently read-only rail configuration."""
        if self.chain_id <= 0:
            raise ValueError("rail chain_id must be positive")
        if re.fullmatch(r"0x[0-9a-fA-F]{40}", self.token_address) is None:
            raise ValueError("rail token_address must be an exact EVM address")
        if type(self.decimals) is not int or not 0 <= self.decimals <= 36:
            raise ValueError("rail decimals must be within [0, 36]")
        if self.signing_enabled:
            raise ValueError("signing is disabled for every reconciliation rail")
        if self.attestation.expected_decimals != self.decimals:
            raise ValueError("rail decimals differ from the reviewed attestation")
        if (
            self.attestation.domain_name != self.token_name
            or self.attestation.domain_version != self.token_version
        ):
            raise ValueError("rail domain differs from the reviewed attestation")


_REVIEWED = date(2026, 7, 18)
_CIRCLE_USDC = "https://developers.circle.com/stablecoins/usdc-contract-addresses"
_CIRCLE_EURC = "https://developers.circle.com/stablecoins/eurc-contract-addresses"
_JPYC_NOTICE = "https://corporate.jpyc.co.jp/news/posts/Notice"
_EIP_3009 = "https://eips.ethereum.org/EIPS/eip-3009"
_BASE_RPC = "https://mainnet.base.org"


def _attestation(
    *,
    sources: tuple[str, ...],
    network_class: str,
    proxy_kind: str,
    decimals: int,
    domain_name: str | None,
    domain_version: str | None,
    calibrated: bool | None = None,
    reviewed_block_number: int | None = None,
    reviewed_block_hash: str | None = None,
    expected_code_sha256: str | None = None,
    implementation_address: str | None = None,
    proxy_implementation_slot: str | None = None,
    implementation_code_sha256: str | None = None,
) -> RailAttestation:
    """Construct a versioned rail attestation from reviewed metadata."""
    return RailAttestation(
        version="2026-07-18.2",
        reviewed_on=_REVIEWED,
        authoritative_sources=sources,
        interface="eip3009",
        network_class=network_class,
        proxy_kind=proxy_kind,
        expected_decimals=decimals,
        domain_name=domain_name,
        domain_version=domain_version,
        reviewed_block_number=reviewed_block_number,
        reviewed_block_hash=reviewed_block_hash,
        expected_code_sha256=expected_code_sha256,
        implementation_address=implementation_address,
        proxy_implementation_slot=proxy_implementation_slot,
        implementation_code_sha256=implementation_code_sha256,
        # Public rails intentionally remain uncalibrated until a reviewed block,
        # runtime-code hash and proxy implementation are independently captured.
        calibrated=network_class == "local" if calibrated is None else calibrated,
    )


KNOWN_RAILS: dict[str, RailConfig] = {
    "mock-anvil": RailConfig(
        "mock-anvil",
        "Local MockUSDC (Anvil)",
        84532,
        "0x5FbDB2315678afecb367f032d93F642f64180aa3",
        6,
        "USDC",
        "2",
        FinalityPolicy("latest", 1),
        _attestation(
            sources=(_EIP_3009,),
            network_class="local",
            proxy_kind="none",
            decimals=6,
            domain_name="USDC",
            domain_version="2",
        ),
    ),
    "usdc-base": RailConfig(
        "usdc-base",
        "USDC on Base",
        8453,
        "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        6,
        "USD Coin",
        "2",
        FinalityPolicy("finalized", 1),
        _attestation(
            sources=(_CIRCLE_USDC, _EIP_3009, _BASE_RPC),
            network_class="mainnet",
            proxy_kind="fiat-token-proxy",
            decimals=6,
            domain_name="USD Coin",
            domain_version="2",
            calibrated=True,
            reviewed_block_number=48_783_151,
            reviewed_block_hash="0x958d7bd04181a2d5b4ae239e5ecdf8944fe46fe25cedac2bb961a081aa822edb",
            expected_code_sha256="98d785fcb1bf847f287adc2310759fd94cc13e754b974bc72131382e8266f607",
            implementation_address="0x2ce6311ddae708829bc0784c967b7d77d19fd779",
            proxy_implementation_slot="0x7050c9e0f4ca769c69bd3a8ef740bc37934f8e2c036e5a723fd8ee048ed3f8c3",
            implementation_code_sha256="dcb3b7ca28662970d0a7cdad420e529fb837d7bf8a246b1a680c20e153db79e8",
        ),
    ),
    "jpyc-polygon": RailConfig(
        "jpyc-polygon",
        "JPYC on Polygon",
        137,
        "0xe7c3d8c9a439fede00d2600032d5db0be71c3c29",
        18,
        "JPY Coin",
        "1",
        FinalityPolicy("finalized", 1),
        _attestation(
            sources=(_JPYC_NOTICE, _EIP_3009),
            network_class="mainnet",
            proxy_kind="vendor-proxy",
            decimals=18,
            domain_name="JPY Coin",
            domain_version="1",
        ),
    ),
    "eurc-base": RailConfig(
        "eurc-base",
        "EURC on Base",
        8453,
        "0x60a3e35cc302bfa44cb288bc5a4f316fdb1adb42",
        6,
        None,
        None,
        FinalityPolicy("finalized", 1),
        _attestation(
            sources=(_CIRCLE_EURC, _EIP_3009, _BASE_RPC),
            network_class="mainnet",
            proxy_kind="fiat-token-proxy",
            decimals=6,
            domain_name=None,
            domain_version=None,
            calibrated=True,
            reviewed_block_number=48_783_151,
            reviewed_block_hash="0x958d7bd04181a2d5b4ae239e5ecdf8944fe46fe25cedac2bb961a081aa822edb",
            expected_code_sha256="c9cf7c3f11c4d3d818801b5a965cea3bae6ff3b9b923242b91a9b4e5888e7835",
            implementation_address="0x2ce6311ddae708829bc0784c967b7d77d19fd779",
            proxy_implementation_slot="0x7050c9e0f4ca769c69bd3a8ef740bc37934f8e2c036e5a723fd8ee048ed3f8c3",
            implementation_code_sha256="dcb3b7ca28662970d0a7cdad420e529fb837d7bf8a246b1a680c20e153db79e8",
        ),
    ),
}


def get_rail(key: str) -> RailConfig:
    """Look up a reviewed rail by exact key."""
    try:
        return KNOWN_RAILS[key]
    except KeyError:
        raise KeyError(f"unknown rail {key!r}; known: {', '.join(sorted(KNOWN_RAILS))}") from None


def token_for_rail(rail: RailConfig, rpc: RpcClient) -> TokenView:
    """Create a read-only token handle.  Runtime attestation occurs on use."""
    return TokenView(rpc=rpc, address=rail.token_address)


@dataclass(frozen=True)
class ChainEvidence:
    """Reproducible provenance for a chain-derived payment verdict."""

    chain_id: int
    finality_block_number: int
    finality_block_hash: str
    finality_block_tag: str
    confirmations: int
    settlement_block_number: int
    settlement_block_hash: str
    transaction_hash: str
    receipt_status: int
    log_index: int | None
    authorization_log_index: int | None
    token_address: str
    token_code_sha256: str
    implementation_address: str | None
    implementation_code_sha256: str | None
    rail_attestation_version: str
    payer: str
    payee: str
    nonce: str
    event_value: int | None
    required_amount: int
    received_amount: int
    payer_balance_before: int
    payer_balance_after: int
    payee_balance_before: int
    payee_balance_after: int
    nonce_consumed: bool
    removed: bool


@dataclass(frozen=True)
class LiveReconciliation:
    """A divergence plus the immutable evidence used to derive it."""

    divergence: Divergence
    evidence: ChainEvidence

    @property
    def kind(self) -> DivergenceKind:
        """Expose the underlying divergence kind."""
        return self.divergence.kind

    @property
    def severity(self) -> Severity:
        """Expose the underlying divergence severity."""
        return self.divergence.severity

    @property
    def message(self) -> str:
        """Expose the human-readable divergence explanation."""
        return self.divergence.message

    @property
    def is_failure(self) -> bool:
        """Return whether the reconciliation found a critical mismatch."""
        return self.divergence.is_failure


@dataclass(frozen=True)
class RailDriftCheck:
    """Machine-readable result of one read-only runtime metadata observation."""

    rail_key: str
    chain_id: int
    block_number: int
    block_hash: str
    code_sha256: str
    expected_code_sha256: str | None
    implementation_address: str | None
    implementation_code_sha256: str | None
    attestation_version: str
    calibrated: bool
    matches: bool
    reason: str

    def as_dict(self) -> dict[str, object]:
        """Serialize the observation as stable read-only drift evidence."""
        return {
            "railKey": self.rail_key,
            "chainId": self.chain_id,
            "blockNumber": self.block_number,
            "blockHash": self.block_hash,
            "codeSha256": self.code_sha256,
            "expectedCodeSha256": self.expected_code_sha256,
            "implementationAddress": self.implementation_address,
            "implementationCodeSha256": self.implementation_code_sha256,
            "attestationVersion": self.attestation_version,
            "calibrated": self.calibrated,
            "matches": self.matches,
            "reason": self.reason,
            "readOnly": True,
        }


def _quantity(value: object, what: str) -> int:
    """Decode a canonical JSON-RPC quantity used as chain evidence."""
    if (
        not isinstance(value, str)
        or re.fullmatch(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)", value) is None
    ):
        raise ChainEvidenceError(f"{what} is not a canonical hex quantity")
    return int(value, 16)


def _exact_hash(value: object, what: str) -> str:
    """Validate and normalize an exact 32-byte evidence hash."""
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise ChainEvidenceError(f"{what} is not an exact 32-byte hash")
    return value.lower()


def _block_identity(block: dict[str, object], what: str) -> tuple[int, str]:
    """Extract an exact block number and hash pair from an RPC block."""
    return _quantity(block.get("number"), f"{what} number"), _exact_hash(
        block.get("hash"), f"{what} hash"
    )


def _code_fingerprint(code: str) -> str:
    """Hash non-empty EVM runtime bytecode with SHA-256."""
    if not isinstance(code, str) or not code.startswith("0x") or code == "0x":
        raise ChainEvidenceError("rail token has no deployed runtime bytecode")
    try:
        raw = bytes.fromhex(code[2:])
    except ValueError as exc:
        raise ChainEvidenceError("rail token returned malformed runtime bytecode") from exc
    if not raw:
        raise ChainEvidenceError("rail token has no deployed runtime bytecode")
    return hashlib.sha256(raw).hexdigest()


def _implementation_identity(
    rail: RailConfig, rpc: RpcClient, block_number: int
) -> tuple[str | None, str | None]:
    """Verify a proxy implementation slot, address, and runtime-code hash."""
    attestation = rail.attestation
    if attestation.proxy_kind == "none":
        return None, None
    slot = attestation.proxy_implementation_slot
    expected_address = attestation.implementation_address
    expected_hash = attestation.implementation_code_sha256
    if slot is None or expected_address is None or expected_hash is None:
        raise ChainEvidenceError("proxy implementation identity is not calibrated")
    word = rpc.call("eth_getStorageAt", [rail.token_address, slot, hex(block_number)])
    if not isinstance(word, str) or _HASH_RE.fullmatch(word) is None:
        raise ChainEvidenceError("proxy implementation slot returned a malformed word")
    if word[2:26] != "0" * 24:
        raise ChainEvidenceError("proxy implementation slot is not an address word")
    observed = "0x" + word[-40:].lower()
    if observed != expected_address.lower():
        raise ChainEvidenceError(
            f"proxy implementation drift: expected {expected_address.lower()}, observed {observed}"
        )
    implementation_hash = _code_fingerprint(rpc.get_code(observed, block_number))
    if implementation_hash != expected_hash:
        raise ChainEvidenceError("proxy implementation runtime code differs from attestation")
    return observed, implementation_hash


def _verify_review_anchor(rail: RailConfig, rpc: RpcClient) -> None:
    """Ensure the attestation's reviewed block remains canonical."""
    attestation = rail.attestation
    if not attestation.calibrated or attestation.reviewed_block_number is None:
        return
    reviewed = rpc.get_block(attestation.reviewed_block_number)
    number, block_hash = _block_identity(reviewed, "reviewed attestation block")
    if number != attestation.reviewed_block_number or block_hash != attestation.reviewed_block_hash:
        raise ChainEvidenceError("reviewed rail-attestation block is not canonical")


def check_rail_drift(rail: RailConfig, rpc: RpcClient) -> RailDriftCheck:
    """Observe one rail at its safe block without signing or changing chain state."""
    live_chain = rpc.chain_id()
    if live_chain != rail.chain_id:
        raise ChainEvidenceError(
            f"RPC chain mismatch: rail requires {rail.chain_id}, node reports {live_chain}"
        )
    _verify_review_anchor(rail, rpc)
    block = rpc.get_block(rail.finality.block_tag)
    number, block_hash = _block_identity(block, "drift-check block")
    code_sha = _code_fingerprint(rpc.get_code(rail.token_address, number))
    implementation_address, implementation_hash = _implementation_identity(rail, rpc, number)
    # Callable-interface probes are pinned to the same block.  The zero account
    # is only an input to balance/state view methods; no transaction is created.
    view = TokenView(rpc, rail.token_address)
    view.balance_of("0x" + "00" * 20, block=number)
    view.authorization_used("0x" + "00" * 20, "0x" + "00" * 32, block=number)
    expected = rail.attestation.expected_code_sha256
    if rail.attestation.network_class == "local" and rail.attestation.calibrated:
        matches = True
        reason = "ephemeral local deployment exposes the required pinned interface"
    elif not rail.attestation.calibrated or expected is None:
        matches = False
        reason = "rail is uncalibrated: reviewed code/proxy identity is not pinned"
    elif code_sha != expected:
        matches = False
        reason = "runtime code hash differs from the reviewed attestation"
    else:
        matches = True
        reason = "runtime interface and code identity match the reviewed attestation"
    return RailDriftCheck(
        rail_key=rail.key,
        chain_id=live_chain,
        block_number=number,
        block_hash=block_hash,
        code_sha256=code_sha,
        expected_code_sha256=expected,
        implementation_address=implementation_address,
        implementation_code_sha256=implementation_hash,
        attestation_version=rail.attestation.version,
        calibrated=rail.attestation.calibrated,
        matches=matches,
        reason=reason,
    )


def _authorization_log_index(
    receipt: dict[str, object], *, token: str, payer: str, nonce: str
) -> int:
    """Locate the unique non-removed authorization log for payer and nonce."""
    expected_topics = [TOPIC_AUTHORIZATION_USED, topic_addr(payer), nonce.lower()]
    matches: list[int] = []
    logs = receipt.get("logs")
    if not isinstance(logs, list):
        raise ChainEvidenceError("receipt logs are missing")
    for raw in logs:
        if not isinstance(raw, dict):
            raise ChainEvidenceError("receipt contains a malformed log")
        if str(raw.get("address", "")).lower() != token.lower():
            continue
        topics = raw.get("topics")
        if topics == expected_topics:
            if raw.get("removed") is not False:
                raise ChainEvidenceError("authorization evidence was removed by a reorg")
            matches.append(_quantity(raw.get("logIndex"), "authorization log index"))
    if len(matches) != 1:
        raise ChainEvidenceError(f"expected one exact AuthorizationUsed log, found {len(matches)}")
    return matches[0]


def _selected_transfer(
    receipt: dict[str, object], *, chain_id: int, log_index: int, token: str
) -> OnChainCredit:
    """Decode the unique receipt log identified by the caller's log index."""
    logs = receipt.get("logs")
    if not isinstance(logs, list):
        raise ChainEvidenceError("receipt logs are missing")
    selected: list[dict[str, object]] = []
    for raw in logs:
        if not isinstance(raw, dict):
            raise ChainEvidenceError("receipt contains a malformed log")
        if _quantity(raw.get("logIndex"), "receipt log index") == log_index:
            selected.append(raw)
    if len(selected) != 1:
        raise ChainEvidenceError(f"receipt has no unique logIndex {log_index}")
    try:
        credit = decode_transfer_log(selected[0], chain_id=chain_id)
    except ReconciliationError as exc:
        raise ChainEvidenceError(f"selected receipt log is not an exact Transfer: {exc}") from exc
    if credit.asset != token.lower():
        raise ChainEvidenceError("selected Transfer was emitted by a different token")
    return credit


def _ensure_no_same_block_race(
    token: TokenView, *, credit: OnChainCredit, payer: str, payee: str
) -> None:
    """Reject same-block transfers that make balance attribution ambiguous."""
    inbound = token.rpc.get_logs(
        address=token.address,
        topics=[TOPIC_TRANSFER, None, topic_addr(payee)],
        from_block=credit.block_number,
        to_block=credit.block_number,
    )
    outbound = token.rpc.get_logs(
        address=token.address,
        topics=[TOPIC_TRANSFER, topic_addr(payer), None],
        from_block=credit.block_number,
        to_block=credit.block_number,
    )
    identities: set[tuple[str, int]] = set()
    for raw in [*inbound, *outbound]:
        try:
            observed = decode_transfer_log(raw, chain_id=credit.chain_id)
        except ReconciliationError as exc:
            raise ChainEvidenceError(f"malformed same-block Transfer evidence: {exc}") from exc
        identities.add((observed.tx_hash, observed.log_index))
        if observed.removed:
            raise ChainEvidenceError("same-block Transfer evidence includes a removed log")
    if identities != {(credit.tx_hash, credit.log_index)}:
        raise ChainEvidenceError(
            "same-block payer/payee transfer race makes attribution inconclusive"
        )


def reconcile_live(
    token: TokenView,
    rail: RailConfig,
    *,
    payer: str,
    payee: str,
    nonce: str,
    transaction_hash: str,
    log_index: int,
    required_amount: int,
    payer_before: int,
    payee_before: int,
    sut_believes_paid: bool,
) -> LiveReconciliation:
    """Prove and grade one exact settlement from a canonical pinned snapshot."""
    if token.address.lower() != rail.token_address.lower():
        raise ChainEvidenceError("token handle does not match the selected rail")
    tx_hash = _exact_hash(transaction_hash, "transaction hash")
    if not isinstance(nonce, str) or _BYTES32_RE.fullmatch(nonce) is None:
        raise ValueError("nonce must be exactly 32 bytes of 0x-prefixed hex")
    if type(log_index) is not int or not 0 <= log_index <= _UINT256_MAX:
        raise ValueError("log_index must be a uint256")
    for value, name, positive in (
        (required_amount, "required_amount", True),
        (payer_before, "payer_before", False),
        (payee_before, "payee_before", False),
    ):
        minimum = 1 if positive else 0
        if type(value) is not int or not minimum <= value <= _UINT256_MAX:
            raise ValueError(f"{name} must be a {'positive ' if positive else ''}uint256")
    if type(sut_believes_paid) is not bool:
        raise ValueError("sut_believes_paid must be a boolean")

    rpc = token.rpc
    live_chain = rpc.chain_id()
    if live_chain != rail.chain_id:
        raise ChainEvidenceError(
            f"RPC chain mismatch: rail requires {rail.chain_id}, node reports {live_chain}"
        )
    if rail.attestation.network_class == "mainnet" and not rail.attestation.calibrated:
        raise ChainEvidenceError(
            "live rail is uncalibrated: reviewed block/code/proxy identity is not pinned"
        )
    _verify_review_anchor(rail, rpc)

    finality_block = rpc.get_block(rail.finality.block_tag)
    finality_number, finality_hash = _block_identity(finality_block, "finality block")
    receipt = rpc.get_transaction_receipt(tx_hash)
    receipt_tx = _exact_hash(receipt.get("transactionHash"), "receipt transaction hash")
    if receipt_tx != tx_hash:
        raise ChainEvidenceError("receipt transaction hash does not match the requested settlement")
    settlement_number = _quantity(receipt.get("blockNumber"), "receipt block number")
    settlement_hash = _exact_hash(receipt.get("blockHash"), "receipt block hash")
    receipt_status = _quantity(receipt.get("status"), "receipt status")
    confirmations = finality_number - settlement_number + 1
    if confirmations < rail.finality.minimum_confirmations:
        raise ChainEvidenceError(
            f"settlement has {confirmations} confirmations; {rail.finality.minimum_confirmations} required"
        )

    canonical = rpc.get_block(settlement_number)
    canonical_number, canonical_hash = _block_identity(canonical, "settlement block")
    if canonical_number != settlement_number or canonical_hash != settlement_hash:
        raise ChainEvidenceError("receipt block is no longer canonical")

    token_code = _code_fingerprint(rpc.get_code(token.address, settlement_number))
    expected_code = rail.attestation.expected_code_sha256
    if expected_code is not None and token_code != expected_code:
        raise ChainEvidenceError("rail token runtime code does not match its attestation")
    implementation_address, implementation_hash = _implementation_identity(
        rail, rpc, settlement_number
    )

    parent_block = settlement_number - 1
    if parent_block < 0:
        raise ChainEvidenceError("genesis-block settlements cannot be attributed safely")
    payer_parent = token.balance_of(payer, block=parent_block)
    payee_parent = token.balance_of(payee, block=parent_block)
    payer_after = token.balance_of(payer, block=settlement_number)
    payee_after = token.balance_of(payee, block=settlement_number)
    nonce_consumed = token.authorization_used(payer, nonce, block=settlement_number)
    if payer_before != payer_parent or payee_before != payee_parent:
        raise ChainEvidenceError("caller before-balances do not match the pinned parent block")

    selected_log: OnChainCredit | None = None
    auth_log_index: int | None = None
    event_value: int | None = None
    received_amount = 0
    if receipt_status == 1:
        selected_log = _selected_transfer(
            receipt, chain_id=live_chain, log_index=log_index, token=token.address
        )
        if selected_log.removed:
            raise ChainEvidenceError("selected Transfer was removed by a reorg")
        if (
            selected_log.tx_hash != tx_hash
            or selected_log.block_hash != settlement_hash
            or selected_log.block_number != settlement_number
            or selected_log.payer != payer.lower()
            or selected_log.payee != payee.lower()
        ):
            raise ChainEvidenceError("selected Transfer does not match the requested payment")
        auth_log_index = _authorization_log_index(
            receipt, token=token.address, payer=payer, nonce=nonce
        )
        if not nonce_consumed:
            raise ChainEvidenceError(
                "receipt emitted AuthorizationUsed but pinned nonce state is false"
            )
        _ensure_no_same_block_race(token, credit=selected_log, payer=payer, payee=payee)
        event_value = selected_log.value
        payer_delta = payer_after - payer_parent
        received_amount = payee_after - payee_parent
        if payer_delta != -event_value or not 0 < received_amount <= event_value:
            raise ChainEvidenceError(
                "pinned balance changes cannot be attributed uniquely to the selected Transfer"
            )
        truth = SettlementTruth(
            nonce_consumed=True,
            payer_balance_after=payer_after,
            payee_balance_after=payee_after,
            payer_delta=payer_delta,
            payee_delta=received_amount,
        )
    else:
        # A reverted exact transaction cannot have transferred this token.  Aggregate
        # same-block balances are retained as evidence but never attributed to it.
        if nonce_consumed:
            raise ChainEvidenceError(
                "reverted receipt but nonce is consumed by another settlement; attribution is inconclusive"
            )
        truth = SettlementTruth(
            nonce_consumed=False,
            payer_balance_after=payer_after,
            payee_balance_after=payee_after,
            payer_delta=0,
            payee_delta=0,
        )

    # Detect a reorg/replacement during the observation window.
    receipt_after = rpc.get_transaction_receipt(tx_hash)
    canonical_after = rpc.get_block(settlement_number)
    _, canonical_hash_after = _block_identity(canonical_after, "rechecked settlement block")
    if (
        _exact_hash(receipt_after.get("blockHash"), "rechecked receipt block hash")
        != settlement_hash
        or canonical_hash_after != settlement_hash
    ):
        raise ChainEvidenceError("settlement was reorged or replaced during observation")

    divergence = detect_payment_divergence(truth, sut_believes_paid, required_amount)
    return LiveReconciliation(
        divergence=divergence,
        evidence=ChainEvidence(
            chain_id=live_chain,
            finality_block_number=finality_number,
            finality_block_hash=finality_hash,
            finality_block_tag=rail.finality.block_tag,
            confirmations=confirmations,
            settlement_block_number=settlement_number,
            settlement_block_hash=settlement_hash,
            transaction_hash=tx_hash,
            receipt_status=receipt_status,
            log_index=selected_log.log_index if selected_log else None,
            authorization_log_index=auth_log_index,
            token_address=token.address.lower(),
            token_code_sha256=token_code,
            implementation_address=implementation_address,
            implementation_code_sha256=implementation_hash,
            rail_attestation_version=rail.attestation.version,
            payer=payer.lower(),
            payee=payee.lower(),
            nonce=nonce.lower(),
            event_value=event_value,
            required_amount=required_amount,
            received_amount=received_amount,
            payer_balance_before=payer_parent,
            payer_balance_after=payer_after,
            payee_balance_before=payee_parent,
            payee_balance_after=payee_after,
            nonce_consumed=nonce_consumed,
            removed=selected_log.removed if selected_log else False,
        ),
    )
