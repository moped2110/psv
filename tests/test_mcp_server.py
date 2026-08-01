"""Tests for the psv MCP surface.

psv never originates a transaction, so there is no signing path to police here.
What needed asserting instead is that the chain a verdict is proven against stays
the operator's choice, and that nothing in this surface can turn an RPC failure
into a verdict.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from psv import mcp_server

pytest.importorskip("mcp", reason="the MCP server is an optional extra")


def call(name: str, arguments: dict[str, Any]) -> Any:
    """Invoke one tool and return its structured result."""
    result = asyncio.run(mcp_server.build_server().call_tool(name, arguments))
    return getattr(result, "structured_content", None) or result


def schemas() -> dict[str, dict[str, Any]]:
    """Every advertised tool's input schema, by tool name."""
    tools = asyncio.run(mcp_server.build_server().list_tools())
    return {t.name: dict(t.input_schema or {}) for t in tools}


def settlement_args(rail: str = "mock-anvil") -> dict[str, Any]:
    """A complete argument set for one reconciliation."""
    return {
        "rail": rail,
        "payer": "0x" + "11" * 20,
        "payee": "0x" + "22" * 20,
        "nonce": "0x" + "33" * 32,
        "transaction_hash": "0x" + "44" * 32,
        "log_index": 0,
        "required_amount": 1000,
        "payer_before": 5000,
        "payee_before": 0,
        "sut_believes_paid": True,
    }


def test_no_tool_lets_the_caller_name_the_rpc_endpoint() -> None:
    """A verdict is worth what the chain it was read from is worth.

    An agent choosing the node could be pointed at one that lies, and it would
    make this server a request primitive aimed at whatever host was in a prompt.
    """
    for name, schema in schemas().items():
        parameters = set(schema.get("properties", {}))
        assert "rpc_url" not in parameters, f"{name} exposes rpc_url"
        assert "endpoint" not in parameters, f"{name} exposes endpoint"


def test_the_surface_is_the_three_read_only_tools() -> None:
    """List, prove, observe. Nothing that writes."""
    assert set(schemas()) == {"list_rails", "reconcile_settlement", "rail_drift"}


def test_the_rails_can_be_listed_without_a_node(monkeypatch) -> None:
    """Finding the rail key must not require the network or configuration."""
    monkeypatch.delenv("PSV_RPC_URL", raising=False)
    result = call("list_rails", {})

    keys = [rail["key"] for rail in result["rails"]]
    assert keys == sorted(keys)
    assert keys
    for rail in result["rails"]:
        assert rail["chain_id"]
        assert rail["token_address"]


def test_a_missing_endpoint_is_explained_rather_than_crashed(monkeypatch) -> None:
    """The operator forgot a variable. That is worth saying in one sentence."""
    monkeypatch.delenv("PSV_RPC_URL", raising=False)

    for name, arguments in (
        ("reconcile_settlement", settlement_args()),
        ("rail_drift", {"rail": "mock-anvil"}),
    ):
        result = call(name, arguments)
        assert "PSV_RPC_URL" in result["error"], name


def test_an_unknown_rail_names_the_ones_that_exist(monkeypatch) -> None:
    """A typo should not send somebody to the source to find the valid keys."""
    monkeypatch.setenv("PSV_RPC_URL", "http://127.0.0.1:8545")
    result = call("rail_drift", {"rail": "no-such-rail"})

    assert "unknown rail" in result["error"]
    assert "known:" in result["error"]


def test_an_rpc_failure_never_becomes_a_verdict(monkeypatch) -> None:
    """Fail closed. A verification tool that guesses is worse than one that stops.

    The endpoint here refuses connections, which is the ordinary case of a node
    being down or misconfigured.
    """
    monkeypatch.setenv("PSV_RPC_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("PSV_RPC_TIMEOUT", "1")
    result = call("reconcile_settlement", settlement_args())

    assert "error" in result
    assert "divergence" not in result


def test_a_nonsense_timeout_falls_back_instead_of_failing(monkeypatch) -> None:
    """A bad value in the environment should not take the whole server down."""
    monkeypatch.setenv("PSV_RPC_TIMEOUT", "not-a-number")
    assert mcp_server._rpc_timeout() == mcp_server.DEFAULT_RPC_TIMEOUT


def test_the_instructions_state_that_nothing_writes() -> None:
    """The client shows these to the model, which is where the posture belongs."""
    assert "nothing signs, sends or moves value" in mcp_server.INSTRUCTIONS
    assert "set by the operator" in mcp_server.INSTRUCTIONS
