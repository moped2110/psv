"""An MCP server exposing psv's read-only verification surface to coding agents.

A payment system holds a belief about an order — paid or unpaid. psv proves the
settlement independently from the chain and names the divergence when the two
disagree: silent loss, phantom credit, underpaid credit. Someone debugging that
in an editor should be able to ask for the proof directly instead of assembling a
long command line by hand.

Everything here is read-only. psv is a verifier: it observes chain state and never
originates a transaction, so unlike the conformance engine there is no signing
path to keep an agent away from. Two things still needed deciding.

**The RPC endpoint is not a tool parameter.** It comes from ``PSV_RPC_URL``,
because which chain a verdict is proven against is a property of the deployment,
not a per-call choice. An agent naming its own endpoint could be pointed at a
node that lies, and a verdict is only worth as much as the chain it was read
from; it would also make this server a general-purpose request primitive aimed at
whatever host appeared in a prompt.

**A verdict without its evidence is a rumour.** Every reconciliation returns the
divergence *and* the identity it was proven against — chain, block, transaction,
log index, token, payer, payee, amount — so a caller can check the claim rather
than believe it.
"""

from __future__ import annotations

import json
import os
from typing import Any

TRANSPORTS = ("stdio", "sse", "streamable-http")
DEFAULT_RPC_TIMEOUT = 10.0

INSTRUCTIONS = """\
Independent settlement verification for x402 payment systems.

reconcile_settlement proves one exact settlement against the chain and compares
it with what the system under test believes. It returns a divergence — consistent
paid, consistent unpaid, silent loss, phantom credit, underpaid credit — together
with the evidence it was proven from.

list_rails shows which reviewed rails this deployment can verify against, and
rail_drift observes a rail's deployed runtime code read-only.

Everything here reads; nothing signs, sends or moves value. The RPC endpoint is
set by the operator, not chosen per call.\
"""


class ConfigurationError(Exception):
    """The server is missing configuration only the operator can supply."""


def _rpc_endpoint() -> str:
    """Resolve the operator-configured RPC endpoint, or say what is missing."""
    endpoint = os.environ.get("PSV_RPC_URL", "").strip()
    if not endpoint:
        raise ConfigurationError(
            "no RPC endpoint configured — set PSV_RPC_URL to the node this "
            "deployment verifies against. It is deliberately not a tool argument: "
            "a verdict is only worth as much as the chain it was read from."
        )
    return endpoint


def _rpc_timeout() -> float:
    """Resolve the RPC timeout, falling back to the documented default."""
    try:
        return float(os.environ.get("PSV_RPC_TIMEOUT", DEFAULT_RPC_TIMEOUT))
    except ValueError:
        return DEFAULT_RPC_TIMEOUT


def build_server() -> Any:
    """Construct the MCP server with the read-only verification tools bound.

    The MCP import is local so the optional dependency is only needed by somebody
    starting the server, not by importing the package.
    """
    from mcp.server.mcpserver import MCPServer

    from . import __version__

    server = MCPServer(
        name="psv",
        title="psv — payment-system verification",
        version=str(__version__),
        instructions=INSTRUCTIONS,
    )

    @server.tool(
        description=(
            "List the reviewed rails this deployment can verify against, with "
            "their chain and token identity. Offline — no RPC call. Call this "
            "first to find the rail key reconcile_settlement needs."
        )
    )
    def list_rails() -> dict[str, Any]:
        """Return the reviewed rail registry."""
        from .rails import KNOWN_RAILS

        return {
            "rails": [
                {
                    "key": key,
                    "label": rail.label,
                    "chain_id": rail.chain_id,
                    "token_address": rail.token_address,
                    "decimals": rail.decimals,
                }
                for key, rail in sorted(KNOWN_RAILS.items())
            ]
        }

    @server.tool(
        description=(
            "Prove one settlement against the chain and compare it with what the "
            "system believes. Read-only. Needs the rail key, the exact payer and "
            "payee addresses, the EIP-3009 nonce, the settlement transaction hash "
            "and Transfer log index, the invoice amount in atomic units, the payer "
            "and payee balances before settlement, and whether the system considers "
            "the order paid. Returns the divergence and the evidence behind it."
        )
    )
    def reconcile_settlement(
        rail: str,
        payer: str,
        payee: str,
        nonce: str,
        transaction_hash: str,
        log_index: int,
        required_amount: int,
        payer_before: int,
        payee_before: int,
        sut_believes_paid: bool,
    ) -> dict[str, Any]:
        """Reconcile one settlement against pinned on-chain truth."""
        from .anvil import RpcClient
        from .cli import run_reconcile
        from .rails import get_rail, token_for_rail

        try:
            endpoint = _rpc_endpoint()
        except ConfigurationError as exc:
            return {"error": str(exc)}
        try:
            rail_config = get_rail(rail)
        except KeyError as exc:
            return {"error": str(exc)}

        client = RpcClient(endpoint=endpoint, timeout=_rpc_timeout())
        try:
            report = run_reconcile(
                token_for_rail(rail_config, client),
                rail_config,
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
        except Exception as exc:
            # Fail closed and name the problem. A verification tool that turned an
            # RPC hiccup into a verdict would be worse than one that said nothing.
            return {"error": f"reconciliation could not be completed: {exc}"}
        return _report_dict(report)

    @server.tool(
        description=(
            "Observe a reviewed rail's deployed runtime code and interface, "
            "read-only, to detect drift from what was reviewed."
        )
    )
    def rail_drift(rail: str) -> dict[str, Any]:
        """Observe one rail's deployed runtime for drift from the reviewed state."""
        from .anvil import RpcClient
        from .rails import check_rail_drift, get_rail

        try:
            endpoint = _rpc_endpoint()
        except ConfigurationError as exc:
            return {"error": str(exc)}
        try:
            rail_config = get_rail(rail)
        except KeyError as exc:
            return {"error": str(exc)}

        client = RpcClient(endpoint=endpoint, timeout=_rpc_timeout())
        try:
            check = check_rail_drift(rail_config, client)
        except Exception as exc:
            return {"error": f"drift observation could not be completed: {exc}"}
        # `matches` lifted to the top level: it is the answer, and burying it
        # inside the detail would make a caller hunt for the verdict.
        return {"matches": check.matches, **_as_dict(check)}

    return server


def _report_dict(report: Any) -> dict[str, Any]:
    """Render a reconciliation report as structured data.

    The report carries the divergence and the evidence together on purpose: a
    verdict handed over without the identity it was proven against is a claim the
    caller has no way to check.
    """
    return _as_dict(report)


def _as_dict(value: Any) -> dict[str, Any]:
    """Coerce a report-like object into a JSON-compatible mapping."""
    for attribute in ("to_dict", "as_dict"):
        method = getattr(value, attribute, None)
        if callable(method):
            result = method()
            if isinstance(result, dict):
                return result
    to_json = getattr(value, "to_json", None)
    if callable(to_json):
        decoded = json.loads(to_json())
        if isinstance(decoded, dict):
            return decoded
    if isinstance(value, dict):
        return value
    return {"result": str(value)}


def main() -> None:
    """Start the MCP server on the transport named by ``PSV_MCP_TRANSPORT``."""
    import sys

    transport = os.environ.get("PSV_MCP_TRANSPORT", "stdio")
    if transport not in TRANSPORTS:
        # stderr, not stdout: on stdio the protocol owns stdout, and a stray line
        # there corrupts the stream rather than reaching a human.
        print(
            f"unsupported PSV_MCP_TRANSPORT {transport!r}; expected one of {TRANSPORTS}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    build_server().run(transport=transport)


if __name__ == "__main__":
    main()
