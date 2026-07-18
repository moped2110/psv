"""Versioned reconciliation report with reproducible chain provenance."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from .divergence import Divergence
from .rails import ChainEvidence, RailConfig

REPORT_VERSION = "2.0"
REPORT_SCHEMA_ID = "https://github.com/moped2110/psv/schemas/reconciliation-report-v2.json"
PRIVACY_POLICY = (
    "Contains public on-chain identifiers and caller-supplied payment metadata; "
    "RPC URLs, credentials, signatures, and private keys are never included."
)

_REASON_CODES = {
    "consistent_paid": "PSV-RECON-CONSISTENT-PAID",
    "consistent_unpaid": "PSV-RECON-CONSISTENT-UNPAID",
    "silent_loss": "PSV-RECON-SILENT-LOSS",
    "phantom_credit": "PSV-RECON-PHANTOM-CREDIT",
    "underpaid_credit": "PSV-RECON-UNDERPAID-CREDIT",
}


@dataclass(frozen=True)
class ReconReport:
    """One immutable v2 report including every input used for its verdict."""

    generated_at: str
    rail_key: str
    rail_label: str
    chain_id: int
    token_address: str
    payer: str
    payee: str
    nonce: str
    sut_believes_paid: bool
    kind: str
    severity: str
    reason_code: str
    message: str
    is_failure: bool
    evidence: ChainEvidence

    @classmethod
    def build(
        cls,
        rail: RailConfig,
        *,
        payer: str,
        payee: str,
        nonce: str,
        sut_believes_paid: bool,
        divergence: Divergence,
        evidence: ChainEvidence,
        generated_at: datetime | None = None,
    ) -> ReconReport:
        """Build with an injectable timestamp for deterministic report tests."""
        when = generated_at or datetime.now(UTC)
        if when.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")
        if (
            evidence.chain_id != rail.chain_id
            or evidence.token_address != rail.token_address.lower()
        ):
            raise ValueError("evidence does not match the selected rail")
        return cls(
            generated_at=when.astimezone(UTC).isoformat(),
            rail_key=rail.key,
            rail_label=rail.label,
            chain_id=rail.chain_id,
            token_address=rail.token_address.lower(),
            payer=payer.lower(),
            payee=payee.lower(),
            nonce=nonce.lower(),
            sut_believes_paid=sut_believes_paid,
            kind=divergence.kind.value,
            severity=divergence.severity.value,
            reason_code=_REASON_CODES[divergence.kind.value],
            message=divergence.message,
            is_failure=divergence.is_failure,
            evidence=evidence,
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the report to the stable v2 JSON-compatible contract."""
        evidence = asdict(self.evidence)
        return {
            "$schema": REPORT_SCHEMA_ID,
            "reportVersion": REPORT_VERSION,
            "tool": {"name": "psv", "mode": "reconcile", "readOnly": True},
            "generatedAt": self.generated_at,
            "privacy": {"policy": PRIVACY_POLICY},
            "rail": {
                "key": self.rail_key,
                "label": self.rail_label,
                "chainId": self.chain_id,
                "token": self.token_address,
            },
            "payment": {
                "payer": self.payer,
                "payee": self.payee,
                "nonce": self.nonce,
                "sutBelievesPaid": self.sut_believes_paid,
                "requiredAmount": self.evidence.required_amount,
                "receivedAmount": self.evidence.received_amount,
            },
            "evidence": {
                "chainId": evidence["chain_id"],
                "finalityBlock": {
                    "number": evidence["finality_block_number"],
                    "hash": evidence["finality_block_hash"],
                    "tag": evidence["finality_block_tag"],
                    "confirmations": evidence["confirmations"],
                },
                "settlementBlock": {
                    "number": evidence["settlement_block_number"],
                    "hash": evidence["settlement_block_hash"],
                },
                "transaction": {
                    "hash": evidence["transaction_hash"],
                    "receiptStatus": evidence["receipt_status"],
                    "logIndex": evidence["log_index"],
                    "authorizationLogIndex": evidence["authorization_log_index"],
                    "removed": evidence["removed"],
                },
                "token": {
                    "address": evidence["token_address"],
                    "codeSha256": evidence["token_code_sha256"],
                    "implementationAddress": evidence["implementation_address"],
                    "implementationCodeSha256": evidence["implementation_code_sha256"],
                    "railAttestationVersion": evidence["rail_attestation_version"],
                },
                "authorization": {
                    "payer": evidence["payer"],
                    "payee": evidence["payee"],
                    "nonce": evidence["nonce"],
                    "nonceConsumed": evidence["nonce_consumed"],
                    "eventValue": evidence["event_value"],
                },
                "balances": {
                    "payerBefore": evidence["payer_balance_before"],
                    "payerAfter": evidence["payer_balance_after"],
                    "payeeBefore": evidence["payee_balance_before"],
                    "payeeAfter": evidence["payee_balance_after"],
                    "requiredAmount": evidence["required_amount"],
                    "receivedAmount": evidence["received_amount"],
                },
            },
            "divergence": {
                "kind": self.kind,
                "severity": self.severity,
                "reasonCode": self.reason_code,
                "message": self.message,
                "isFailure": self.is_failure,
            },
        }

    def validate(self) -> None:
        """Dependency-free structural validation for callers without jsonschema."""
        doc = self.to_dict()
        required = {
            "$schema",
            "reportVersion",
            "tool",
            "generatedAt",
            "privacy",
            "rail",
            "payment",
            "evidence",
            "divergence",
        }
        if set(doc) != required or doc["reportVersion"] != REPORT_VERSION:
            raise ValueError("invalid reconciliation report envelope")
        if self.reason_code != _REASON_CODES.get(self.kind):
            raise ValueError("invalid divergence reason code")
        if self.evidence.required_amount <= 0 or self.evidence.received_amount < 0:
            raise ValueError("invalid report amount evidence")
        if self.evidence.chain_id != self.chain_id:
            raise ValueError("report and evidence chain IDs differ")

    def to_json(self) -> str:
        """Validate and render deterministic, human-readable JSON."""
        self.validate()
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    def to_markdown(self) -> str:
        """Validate and render a concise human-readable evidence report."""
        self.validate()
        verdict = "DIVERGENCE" if self.is_failure else "CONSISTENT"
        e = self.evidence
        return "\n".join(
            [
                "# psv reconciliation report",
                "",
                "_Read-only: psv read the chain and compared it to the system's belief. "
                "No funds moved._",
                "",
                f"**Verdict:** {verdict} - `{self.kind}` ({self.severity})",
                f"**Reason code:** `{self.reason_code}`",
                f"**Rail:** {self.rail_label} (`{self.rail_key}`, chainId {self.chain_id})",
                f"**Token:** `{self.token_address}`",
                f"**Payer:** `{self.payer}` -> **Payee:** `{self.payee}`",
                f"**Nonce:** `{self.nonce}`",
                f"**Transaction:** `{e.transaction_hash}` (receipt status {e.receipt_status})",
                f"**Log:** `{e.log_index}`; authorization log `{e.authorization_log_index}`",
                f"**Settlement block:** {e.settlement_block_number} `{e.settlement_block_hash}`",
                f"**Finality:** {e.finality_block_tag} block {e.finality_block_number}, "
                f"{e.confirmations} confirmations",
                f"**Amount:** required {e.required_amount}; received {e.received_amount}; "
                f"event {e.event_value}",
                f"**Balances:** payer {e.payer_balance_before} -> {e.payer_balance_after}; "
                f"payee {e.payee_balance_before} -> {e.payee_balance_after}",
                f"**System believed paid:** {self.sut_believes_paid}",
                "",
                f"> {self.message}",
                "",
                f"Privacy: {PRIVACY_POLICY}",
                "",
            ]
        )

    def as_dict(self) -> dict[str, object]:
        """Backward-compatible alias for the versioned wire document."""
        return self.to_dict()


def exit_code(report: ReconReport) -> int:
    """Return 1 for every critical divergence, otherwise 0."""
    return int(report.is_failure)


def validate_report_document(document: dict[str, Any]) -> None:
    """Validate a deserialized v2 document's envelope and cross-field invariants."""
    if document.get("reportVersion") != REPORT_VERSION:
        raise ValueError(f"unsupported report version: {document.get('reportVersion')!r}")
    if document.get("$schema") != REPORT_SCHEMA_ID:
        raise ValueError("unknown report schema")
    tool = document.get("tool")
    if tool != {"name": "psv", "mode": "reconcile", "readOnly": True}:
        raise ValueError("invalid tool metadata")
    rail = document.get("rail")
    evidence = document.get("evidence")
    if not isinstance(rail, dict) or not isinstance(evidence, dict):
        raise ValueError("rail/evidence must be objects")
    if rail.get("chainId") != evidence.get("chainId"):
        raise ValueError("report and evidence chain IDs differ")
