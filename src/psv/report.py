"""Reconciliation report — render a divergence verdict as JSON or Markdown.

Turns a read-only reconciliation (:func:`psv.rails.reconcile_live` → a
:class:`psv.divergence.Divergence`) into a machine-readable JSON report and a
human-readable Markdown one, so a system-level run produces a shareable artefact
alongside 01's conformance report. Pure formatting — reads nothing, moves nothing.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from .divergence import Divergence
from .rails import RailConfig

REPORT_VERSION = "1.0"


@dataclass(frozen=True)
class ReconReport:
    """A reconciliation outcome for one payment, ready to serialise.

    Carries the rail + payment identity and the graded divergence verdict. The two
    CRITICAL kinds (``silent_loss`` / ``phantom_credit``) set ``is_failure``.
    """

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
    message: str
    is_failure: bool

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
    ) -> ReconReport:
        return cls(
            rail_key=rail.key,
            rail_label=rail.label,
            chain_id=rail.chain_id,
            token_address=rail.token_address,
            payer=payer,
            payee=payee,
            nonce=nonce,
            sut_believes_paid=sut_believes_paid,
            kind=divergence.kind.value,
            severity=divergence.severity.value,
            message=divergence.message,
            is_failure=divergence.is_failure,
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "reportVersion": REPORT_VERSION,
                "tool": {"name": "psv", "mode": "reconcile", "readOnly": True},
                "timestamp": datetime.now(timezone.utc).isoformat(),
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
                },
                "divergence": {
                    "kind": self.kind,
                    "severity": self.severity,
                    "message": self.message,
                    "isFailure": self.is_failure,
                },
            },
            indent=2,
        )

    def to_markdown(self) -> str:
        verdict = "❌ DIVERGENCE" if self.is_failure else "✅ CONSISTENT"
        return "\n".join(
            [
                "# psv reconciliation report",
                "",
                "_Read-only: psv read the chain and compared it to the system's belief. "
                "No funds moved._",
                "",
                f"**Verdict:** {verdict} — `{self.kind}` ({self.severity})",
                f"**Rail:** {self.rail_label} (`{self.rail_key}`, chainId {self.chain_id})",
                f"**Token:** `{self.token_address}`",
                f"**Payer:** `{self.payer}`  →  **Payee:** `{self.payee}`",
                f"**Nonce:** `{self.nonce}`",
                f"**System believed paid:** {self.sut_believes_paid}",
                "",
                f"> {self.message}",
                "",
            ]
        )

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def exit_code(report: ReconReport) -> int:
    """CI gate: 1 on a CRITICAL divergence (silent loss / phantom credit), else 0."""
    return int(report.is_failure)
