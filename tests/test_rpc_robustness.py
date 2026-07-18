"""RPC/CLI robustness: a hostile/unreachable chain is a clean error, not a crash.

The transport normalizes every network/JSON failure to RpcError, and the reconcile
CLI catches it and exits 2 (usage/lookup) with a message — never a raw traceback.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from psv import cli
from psv.anvil import RpcClient, RpcError, _urllib_transport
from psv.chain import TokenView

_REQ = {"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": []}


class _FakeResp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *_a: object) -> bool:
        return False

    def read(self, _size: int = -1) -> bytes:
        return self._body


def test_transport_wraps_network_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> object:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    send = _urllib_transport("http://127.0.0.1:1", 0.5)
    with pytest.raises(RpcError, match="transport failure"):
        send(_REQ)


def test_transport_wraps_non_json_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _FakeResp(b"<<garbage>>"))
    send = _urllib_transport("http://x", 0.5)
    with pytest.raises(RpcError, match="non-JSON"):
        send(_REQ)


def test_chain_malformed_hex_result_is_rpc_error() -> None:
    # A node that returns a JSON-valid but non-hex `result` must surface as RpcError,
    # not a raw ValueError leaking out of the hex parsing.
    def transport(request: dict) -> dict:
        return {"jsonrpc": "2.0", "id": request["id"], "result": "0xNOTHEX"}

    token = TokenView(rpc=RpcClient(transport=transport), address="0x" + "33" * 20)
    with pytest.raises(RpcError, match="eth_call result"):
        token.balance_of("0x" + "11" * 20)


def test_cli_reconcile_rpc_error_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> object:
        raise RpcError("chain unreachable")

    monkeypatch.setattr(cli, "run_reconcile", boom)
    rc = cli.main(
        [
            "reconcile",
            "--rail",
            "usdc-base",
            "--payer",
            "0x" + "11" * 20,
            "--payee",
            "0x" + "22" * 20,
            "--nonce",
            "0x" + "ab" * 32,
            "--payer-before",
            "1000",
            "--payee-before",
            "0",
            "--sut-paid",
        ]
    )
    assert rc == 2
