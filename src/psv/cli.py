"""psv command-line interface — read-only reconciliation.

`psv reconcile` compares one x402 payment against on-chain truth for a known rail
(USDC/JPYC/EURC/…) and prints a graded verdict (consistent / silent-loss /
phantom-credit), optionally as JSON/Markdown.

**Money invariant.** This CLI only *reads* the chain (balances + EIP-3009 nonce
state). It never signs, settles, or transfers — there is no signing import here and
no code path that moves funds. Outbound value in psv is testnet/Anvil only.

Stdlib argparse (psv core stays dependency-light). Exit code 0 = consistent,
1 = a CRITICAL divergence, 2 = a usage/lookup error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from .anvil import RpcClient, RpcError
from .chain import TokenView
from .divergence import Divergence
from .rails import RailConfig, get_rail, reconcile_live, token_for_rail
from .report import ReconReport, exit_code
from .run_record import DEFAULT_LOG_DIR, NO_LOG_ENV


def run_reconcile(
    token: TokenView,
    rail: RailConfig,
    *,
    payer: str,
    payee: str,
    nonce: str,
    payer_before: int,
    payee_before: int,
    sut_believes_paid: bool,
) -> ReconReport:
    """Read-only reconciliation core (chain-injectable via ``token``) → a report."""
    divergence: Divergence = reconcile_live(
        token,
        payer=payer,
        payee=payee,
        nonce=nonce,
        payer_before=payer_before,
        payee_before=payee_before,
        sut_believes_paid=sut_believes_paid,
    )
    return ReconReport.build(
        rail,
        payer=payer,
        payee=payee,
        nonce=nonce,
        sut_believes_paid=sut_believes_paid,
        divergence=divergence,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psv",
        description="System-level x402 verification — read-only reconciliation "
        "(never signs or settles).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    r = sub.add_parser(
        "reconcile",
        help="Read-only: compare one payment against on-chain truth for a known rail.",
    )
    r.add_argument(
        "--rail", required=True, help="Rail key (e.g. usdc-base, jpyc-polygon, eurc-base)"
    )
    r.add_argument("--payer", required=True, help="Payer address")
    r.add_argument("--payee", required=True, help="Payee (merchant) address")
    r.add_argument("--nonce", required=True, help="EIP-3009 authorization nonce (0x…32 bytes)")
    r.add_argument(
        "--payer-before",
        type=int,
        required=True,
        help="Payer token balance (atomic units) BEFORE the payment",
    )
    r.add_argument(
        "--payee-before",
        type=int,
        required=True,
        help="Payee token balance (atomic units) BEFORE the payment",
    )
    belief = r.add_mutually_exclusive_group(required=True)
    belief.add_argument(
        "--sut-paid",
        dest="sut_believes_paid",
        action="store_true",
        help="The system believes this order was paid",
    )
    belief.add_argument(
        "--sut-unpaid",
        dest="sut_believes_paid",
        action="store_false",
        help="The system believes this order is unpaid",
    )
    r.add_argument(
        "--rpc-url", default=None, help="JSON-RPC endpoint (default http://127.0.0.1:8545)"
    )
    r.add_argument(
        "--json", dest="json_out", default=None, help="Write the JSON report to this path"
    )
    r.add_argument(
        "--markdown", dest="md_out", default=None, help="Write the Markdown report to this path"
    )
    r.add_argument(
        "--log-dir",
        dest="log_dir",
        default=None,
        help="Directory for the tamper-evident JSON run record + runs.jsonl journal. "
        "Logging is ON by default (writes to ./psv-runs); use this to change the path.",
    )
    r.add_argument(
        "--no-log",
        dest="no_log",
        action="store_true",
        help="Disable the run record for this run (logging is on by default).",
    )
    return parser


def _resolve_log_dir(args: argparse.Namespace) -> Path | None:
    """Logging is on by default (./psv-runs). --no-log or the NO_LOG_ENV env var
    (used by tests) suppresses the default; an explicit --log-dir always writes."""
    if args.no_log:
        return None
    if args.log_dir:
        return Path(str(args.log_dir))
    if os.environ.get(NO_LOG_ENV):
        return None
    return Path(DEFAULT_LOG_DIR)


def _cmd_reconcile(args: argparse.Namespace) -> int:
    started_at = datetime.now(UTC)
    log_dir = _resolve_log_dir(args)
    inputs = {
        "rail": str(args.rail),
        "payer": str(args.payer),
        "payee": str(args.payee),
        "nonce": str(args.nonce),
        "payer_before": int(args.payer_before),
        "payee_before": int(args.payee_before),
        "sut_believes_paid": bool(args.sut_believes_paid),
        "rpc_url": args.rpc_url,
    }

    def _write(report: dict[str, object] | None, code: int, error: str | None = None) -> None:
        if log_dir is None:
            return
        from .run_record import build_run_record, write_run_record

        record = build_run_record(
            command="reconcile",
            inputs=inputs,
            report=report,
            exit_code=code,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            error=error,
        )
        print(f"Run record: {write_run_record(record, log_dir)}")

    try:
        rail = get_rail(str(args.rail))
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2  # bad --rail arg (usage error) — not a run, not logged

    rpc = RpcClient(endpoint=str(args.rpc_url)) if args.rpc_url else RpcClient()
    try:
        token = token_for_rail(rail, rpc)
        report = run_reconcile(
            token,
            rail,
            payer=str(args.payer),
            payee=str(args.payee),
            nonce=str(args.nonce),
            payer_before=int(args.payer_before),
            payee_before=int(args.payee_before),
            sut_believes_paid=bool(args.sut_believes_paid),
        )
    except RpcError as exc:
        # Chain unreachable / RPC error / malformed response — an environment fault,
        # not a divergence. Record the failed attempt, then exit 2 with a clean message.
        _write(None, 2, error=f"chain/RPC error: {exc}")
        print(f"chain/RPC error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        # Malformed input (e.g. a bad address or non-32-byte nonce caught by the ABI
        # slot encoders) — a usage error, not a crash. Record it and exit 2 cleanly.
        _write(None, 2, error=f"invalid input: {exc}")
        print(f"invalid input: {exc}", file=sys.stderr)
        return 2

    print(report.to_markdown())
    if args.json_out:
        Path(str(args.json_out)).write_text(report.to_json(), encoding="utf-8")
        print(f"JSON report: {args.json_out}")
    if args.md_out:
        Path(str(args.md_out)).write_text(report.to_markdown(), encoding="utf-8")
        print(f"Markdown report: {args.md_out}")

    code = exit_code(report)
    _write(json.loads(report.to_json()), code)
    return code


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "reconcile":
        return _cmd_reconcile(args)
    return 2  # pragma: no cover — subparser is required


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
