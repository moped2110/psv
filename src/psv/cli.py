"""Read-only, fail-closed reconciliation command line interface."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Never
from urllib.parse import urlsplit

from .anvil import RpcClient, RpcError
from .chain import TokenView
from .rails import RailConfig, check_rail_drift, get_rail, reconcile_live, token_for_rail
from .report import ReconReport, exit_code
from .run_record import DEFAULT_LOG_DIR, NO_LOG_ENV

_UINT256_MAX = 2**256 - 1
_MAX_PATH_CHARS = 4096
_MAX_REPORT_BYTES = 2 * 1024 * 1024


class CliUsageError(ValueError):
    """A deterministic command-line validation failure."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        """Convert argparse termination into a deterministic usage exception."""
        raise CliUsageError(message)


def _address(value: str) -> str:
    """Parse an exact EVM address from a CLI argument."""
    if re.fullmatch(r"0x[0-9a-fA-F]{40}", value) is None:
        raise argparse.ArgumentTypeError("must be an exact 20-byte EVM address")
    return value


def _bytes32(value: str) -> str:
    """Parse an exact bytes32 hexadecimal CLI argument."""
    if re.fullmatch(r"0x[0-9a-fA-F]{64}", value) is None:
        raise argparse.ArgumentTypeError("must be exactly 32 bytes of 0x-prefixed hex")
    return value


def _uint256(value: str) -> int:
    """Parse a base-10 uint256 CLI argument."""
    try:
        result = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a base-10 uint256") from exc
    if not 0 <= result <= _UINT256_MAX:
        raise argparse.ArgumentTypeError("must be within uint256")
    return result


def _positive_uint256(value: str) -> int:
    """Parse a strictly positive base-10 uint256 CLI argument."""
    result = _uint256(value)
    if result == 0:
        raise argparse.ArgumentTypeError("must be a positive uint256")
    return result


def _timeout(value: str) -> float:
    """Parse an RPC timeout within the supported safety bounds."""
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not 0.1 <= result <= 120.0:
        raise argparse.ArgumentTypeError("must be within [0.1, 120] seconds")
    return result


def _rpc_url(value: str) -> str:
    """Validate a credential-free bounded HTTP(S) RPC URL."""
    if len(value) > 2048 or "\x00" in value:
        raise argparse.ArgumentTypeError("RPC URL is too long or contains NUL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid RPC URL: {exc}") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise argparse.ArgumentTypeError("RPC URL must use http(s) and include a host")
    if parsed.fragment:
        raise argparse.ArgumentTypeError("RPC URL must not contain a fragment")
    if parsed.username is not None or parsed.password is not None:
        raise argparse.ArgumentTypeError("RPC URL must not contain inline credentials")
    if port is not None and not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("RPC port must be within [1, 65535]")
    return value


def _output_path(value: str) -> Path:
    """Parse a bounded, NUL-free report output path."""
    if not value or len(value) > _MAX_PATH_CHARS or "\x00" in value:
        raise argparse.ArgumentTypeError("output path is empty, too long, or contains NUL")
    return Path(value)


def run_reconcile(
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
    generated_at: datetime | None = None,
) -> ReconReport:
    """Run exact chain reconciliation and build a versioned evidence report."""
    result = reconcile_live(
        token,
        rail,
        payer=payer,
        payee=payee,
        nonce=nonce,
        transaction_hash=transaction_hash,
        log_index=log_index,
        required_amount=required_amount,
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
        divergence=result.divergence,
        evidence=result.evidence,
        generated_at=generated_at,
    )


def _build_parser() -> argparse.ArgumentParser:
    """Build the fail-closed reconciliation and drift command parser."""
    parser = _Parser(
        prog="psv",
        description="System-level x402 verification - read-only reconciliation "
        "(never signs or settles).",
    )
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    r = sub.add_parser(
        "reconcile",
        help="Prove one exact transaction/log against pinned on-chain truth.",
    )
    r.add_argument("--rail", required=True, help="Reviewed rail key")
    r.add_argument("--payer", type=_address, required=True, help="Exact payer EVM address")
    r.add_argument("--payee", type=_address, required=True, help="Exact merchant EVM address")
    r.add_argument("--nonce", type=_bytes32, required=True, help="EIP-3009 bytes32 nonce")
    r.add_argument(
        "--tx-hash", type=_bytes32, required=True, help="Exact settlement transaction hash"
    )
    r.add_argument(
        "--log-index", type=_uint256, required=True, help="Transfer logIndex in the receipt"
    )
    r.add_argument(
        "--required-amount",
        type=_positive_uint256,
        required=True,
        help="Invoice amount in atomic token units (positive uint256)",
    )
    r.add_argument(
        "--payer-before",
        type=_uint256,
        required=True,
        help="Expected payer balance at the settlement parent block",
    )
    r.add_argument(
        "--payee-before",
        type=_uint256,
        required=True,
        help="Expected payee balance at the settlement parent block",
    )
    belief = r.add_mutually_exclusive_group(required=True)
    belief.add_argument("--sut-paid", dest="sut_believes_paid", action="store_true")
    belief.add_argument("--sut-unpaid", dest="sut_believes_paid", action="store_false")
    r.add_argument(
        "--rpc-url",
        type=_rpc_url,
        default="http://127.0.0.1:8545",
        help="Bounded HTTP(S) JSON-RPC endpoint",
    )
    r.add_argument(
        "--rpc-timeout", type=_timeout, default=10.0, help="RPC timeout in seconds [0.1, 120]"
    )
    r.add_argument("--json", dest="json_out", type=_output_path, default=None)
    r.add_argument("--markdown", dest="md_out", type=_output_path, default=None)
    r.add_argument(
        "--log-dir",
        dest="log_dir",
        type=_output_path,
        default=None,
        help="Directory for the integrity-checked run record and runs.jsonl journal",
    )
    r.add_argument("--no-log", dest="no_log", action="store_true")

    drift = sub.add_parser(
        "rail-drift",
        help="Read-only runtime code/interface observation for a reviewed rail.",
    )
    drift.add_argument("--rail", required=True, help="Reviewed rail key")
    drift.add_argument("--rpc-url", type=_rpc_url, required=True)
    drift.add_argument("--rpc-timeout", type=_timeout, default=10.0)
    return parser


def _resolve_log_dir(args: argparse.Namespace) -> Path | None:
    """Resolve audit logging precedence from flags, environment, and defaults."""
    if args.no_log:
        return None
    if args.log_dir:
        return Path(args.log_dir)
    if os.environ.get(NO_LOG_ENV):
        return None
    return Path(DEFAULT_LOG_DIR)


def _write_report(path: Path, payload: str, label: str) -> None:
    """Write a size-bounded report payload, creating parent directories."""
    encoded = payload.encode("utf-8")
    if len(encoded) > _MAX_REPORT_BYTES:
        raise OSError(f"{label} report exceeds {_MAX_REPORT_BYTES} bytes")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)


def _cmd_reconcile(args: argparse.Namespace) -> int:
    """Execute reconciliation, emit reports, and persist an integrity record."""
    started_at = datetime.now(UTC)
    log_dir = _resolve_log_dir(args)
    inputs = {
        "rail": args.rail,
        "payer": args.payer,
        "payee": args.payee,
        "nonce": args.nonce,
        "transaction_hash": args.tx_hash,
        "log_index": args.log_index,
        "required_amount": args.required_amount,
        "payer_before": args.payer_before,
        "payee_before": args.payee_before,
        "sut_believes_paid": args.sut_believes_paid,
        "rpc_url": args.rpc_url,
        "rpc_timeout": args.rpc_timeout,
    }

    def write_record(report: dict[str, object] | None, code: int, error: str | None = None) -> bool:
        """Persist this invocation's record and report whether auditing succeeded."""
        if log_dir is None:
            return True
        from .run_record import build_run_record, write_run_record

        try:
            record = build_run_record(
                command="reconcile",
                inputs=inputs,
                report=report,
                exit_code=code,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                error=error,
            )
            path = write_run_record(record, log_dir)
        except (OSError, ValueError) as exc:
            print(f"audit-record error: {exc}", file=sys.stderr)
            return False
        print(f"Run record: {path}")
        return True

    try:
        rail = get_rail(args.rail)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    rpc = RpcClient(endpoint=args.rpc_url, timeout=args.rpc_timeout)
    try:
        report = run_reconcile(
            token_for_rail(rail, rpc),
            rail,
            payer=args.payer,
            payee=args.payee,
            nonce=args.nonce,
            transaction_hash=args.tx_hash,
            log_index=args.log_index,
            required_amount=args.required_amount,
            payer_before=args.payer_before,
            payee_before=args.payee_before,
            sut_believes_paid=args.sut_believes_paid,
        )
    except (RpcError, ValueError) as exc:
        message = f"reconciliation error: {exc}"
        logged = write_record(None, 2, error=message)
        print(message, file=sys.stderr)
        return 2 if logged else 2

    markdown = report.to_markdown()
    json_payload = report.to_json()
    try:
        if args.json_out is not None:
            _write_report(args.json_out, json_payload, "JSON")
        if args.md_out is not None:
            _write_report(args.md_out, markdown, "Markdown")
    except OSError as exc:
        message = f"output error: {exc}"
        write_record(report.to_dict(), 2, error=message)
        print(message, file=sys.stderr)
        return 2

    print(markdown)
    if args.json_out is not None:
        print(f"JSON report: {args.json_out}")
    if args.md_out is not None:
        print(f"Markdown report: {args.md_out}")

    code = exit_code(report)
    if not write_record(report.to_dict(), code):
        return 2
    return code


def _cmd_rail_drift(args: argparse.Namespace) -> int:
    """Observe a live rail read-only and return a drift-specific exit code."""
    try:
        rail = get_rail(args.rail)
        check = check_rail_drift(rail, RpcClient(endpoint=args.rpc_url, timeout=args.rpc_timeout))
    except (KeyError, RpcError, ValueError) as exc:
        print(f"rail-drift error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(check.as_dict(), indent=2, sort_keys=True))
    return 0 if check.matches else 1


def main(argv: list[str] | None = None) -> int:
    """Dispatch the CLI and convert expected failures into stable exit codes."""
    try:
        args = _build_parser().parse_args(argv)
    except CliUsageError as exc:
        print(f"psv: error: {exc}", file=sys.stderr)
        return 2
    if args.command == "reconcile":
        try:
            return _cmd_reconcile(args)
        except OSError as exc:
            # Broken stdout/stderr pipes and other terminal I/O failures must not
            # masquerade as a successfully emitted/audited report.
            try:
                print(f"output error: {exc}", file=sys.stderr)
            except OSError:
                pass
            return 2
    if args.command == "rail-drift":
        try:
            return _cmd_rail_drift(args)
        except OSError:
            return 2
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
