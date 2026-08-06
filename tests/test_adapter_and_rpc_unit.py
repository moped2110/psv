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

PAYEE = "0x" + "11" * 20
TOKEN = "0x" + "22" * 20
TX_HASH = "0x" + "ab" * 32


def test_parse_quote_normalizes_fields() -> None:
    q = parse_quote(
        {
            "order_id": "ord_1",
            "amount": "10000",
            "payTo": PAYEE,
            "asset": TOKEN,
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
                    "payTo": PAYEE,
                    "asset": TOKEN,
                    "network": "eip155:84532",
                    "extra": {"name": "USDC", "version": "2"},
                },
            )
        if request.url.path == "/pay":
            body = json.loads(request.content)
            assert body["order_id"] == "ord_x"
            return httpx.Response(
                200, json={"order_id": "ord_x", "submitted_tx": TX_HASH, "settled": True}
            )
        if request.url.path == "/status/ord_x":
            return httpx.Response(
                200,
                json={
                    "order_id": "ord_x",
                    "paid": True,
                    "resource": "premium",
                    "submitted_tx": TX_HASH,
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
    rpc.get_logs(address=TOKEN, topics=[TX_HASH, None], from_block=5, to_block="latest")
    flt = seen[0]["params"][0]
    assert flt["address"] == TOKEN
    assert flt["topics"] == [TX_HASH, None]
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


# --- The chain-truth transport refuses redirects -------------------------------
#
# A verdict is worth exactly as much as the chain it was read from. urllib follows
# redirects by default, which would let a redirecting or hijacked provider move the
# read to a host the operator never configured — and would also permit https -> http.
# x402-conformance states this rule in its SECURITY.md; psv had inherited the concern
# without the countermeasure.


def test_a_redirecting_rpc_endpoint_is_refused_not_followed() -> None:
    """The handler raises instead of handing urllib a new request to issue."""
    import urllib.request

    from psv.anvil import _RefuseRedirects

    handler = _RefuseRedirects()
    request = urllib.request.Request("https://rpc.example/v2/sk_live_SECRET")

    try:
        handler.redirect_request(request, None, 302, "Found", {}, "https://elsewhere.example/")
    except RpcError as exc:
        message = str(exc)
    else:  # pragma: no cover - the point of the test
        raise AssertionError("a redirect was accepted")

    assert "refusing to follow" in message
    # The refusal names the host so an operator can act on it, but not the key.
    assert "rpc.example" in message
    assert "sk_live" not in message


def test_the_transport_is_built_with_the_refusing_handler() -> None:
    """The rule has to be wired in, not merely available."""
    import urllib.request

    from psv.anvil import _RefuseRedirects, _urllib_transport

    built: list[Any] = []
    real_build_opener = urllib.request.build_opener

    def spy(*handlers: Any) -> Any:
        """Record which handlers the transport asks for."""
        built.extend(handlers)
        return real_build_opener(*handlers)

    urllib.request.build_opener = spy  # type: ignore[assignment]
    try:
        _urllib_transport("https://rpc.example", timeout=1.0)
    finally:
        urllib.request.build_opener = real_build_opener  # type: ignore[assignment]

    assert _RefuseRedirects in built
