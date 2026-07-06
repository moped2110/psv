"""Offline unit tests for the HTTP SUT adapter and the JSON-RPC client.

The adapter is driven against an httpx MockTransport (a canned SUT); the RPC
client against a fake transport (a canned chain). No network, no chain.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from psv.anvil import RpcClient, RpcError
from psv.sut import HttpSutAdapter, parse_quote


def test_parse_quote_normalizes_fields() -> None:
    q = parse_quote(
        {
            "order_id": "ord_1",
            "amount": "10000",
            "payTo": "0xabc",
            "asset": "0xtok",
            "network": "eip155:84532",
            "extra": {"name": "USDC", "version": "2"},
        }
    )
    assert q.amount == 10_000
    assert q.chain_id == 84532
    assert q.token_name == "USDC"


def test_http_adapter_round_trip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/quote":
            return httpx.Response(
                200,
                json={
                    "order_id": "ord_x",
                    "amount": "10000",
                    "payTo": "0xmerchant",
                    "asset": "0xtok",
                    "network": "eip155:84532",
                    "extra": {"name": "USDC", "version": "2"},
                },
            )
        if request.url.path == "/pay":
            body = json.loads(request.content)
            assert body["order_id"] == "ord_x"
            return httpx.Response(
                200, json={"order_id": "ord_x", "submitted_tx": "0xdead", "settled": True}
            )
        if request.url.path == "/status/ord_x":
            return httpx.Response(
                200,
                json={
                    "order_id": "ord_x",
                    "paid": True,
                    "resource": "premium",
                    "submitted_tx": "0xdead",
                },
            )
        return httpx.Response(404)

    client = httpx.Client(base_url="http://sut.test", transport=httpx.MockTransport(handler))
    adapter = HttpSutAdapter(base_url="http://sut.test", _client=client)

    quote = adapter.quote()
    assert quote.order_id == "ord_x"
    pay = adapter.pay(quote.order_id, {"nonce": "0x01"})
    assert pay.settled is True
    status = adapter.status(quote.order_id)
    assert status.paid and status.resource == "premium"


def _fake_transport(responses: dict[str, Any]):
    seen: list[dict[str, Any]] = []

    def send(request: dict[str, Any]) -> dict[str, Any]:
        seen.append(request)
        method = request["method"]
        if method not in responses:
            return {"jsonrpc": "2.0", "id": request["id"], "error": {"message": f"no {method}"}}
        return {"jsonrpc": "2.0", "id": request["id"], "result": responses[method]}

    return send, seen


def test_rpc_builds_well_formed_requests_and_increments_ids() -> None:
    send, seen = _fake_transport({"evm_snapshot": "0x1", "eth_blockNumber": "0x10"})
    rpc = RpcClient(transport=send)
    assert rpc.snapshot() == "0x1"
    assert rpc.block_number() == 16
    assert [r["id"] for r in seen] == [1, 2]
    assert seen[0]["jsonrpc"] == "2.0" and seen[0]["method"] == "evm_snapshot"


def test_rpc_get_logs_filter_shape() -> None:
    send, seen = _fake_transport({"eth_getLogs": []})
    rpc = RpcClient(transport=send)
    rpc.get_logs(address="0xtok", topics=["0xtopic", None], from_block=5, to_block="latest")
    flt = seen[0]["params"][0]
    assert flt["address"] == "0xtok"
    assert flt["topics"] == ["0xtopic", None]
    assert flt["fromBlock"] == "0x5" and flt["toBlock"] == "latest"


def test_rpc_raises_on_error_response() -> None:
    send, _ = _fake_transport({})  # every method -> error
    rpc = RpcClient(transport=send)
    try:
        rpc.call("eth_chainId")
    except RpcError as exc:
        assert "eth_chainId" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RpcError")
