"""An HTTP 4xx from the RPC must carry the node's own reason, bounded and redacted.

Found on 2026-10-07: ``psv rail-drift --rail usdc-celo-sepolia`` against forno
failed with only "HTTP Error 400: Bad Request". The body said
``block is more than 10064 blocks behind head`` — forno prunes, and rail-drift reads
the reviewed block — but psv discarded it.
"""

from __future__ import annotations

import http.server
import json
import threading
from collections.abc import Iterator

import pytest

from psv.anvil import RpcClient, RpcError


def _serve(status: int, body: bytes) -> Iterator[str]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib hook name
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def pruned_node() -> Iterator[str]:
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "error": {"code": -32602, "message": "block is more than 10064 blocks behind head"},
        }
    ).encode()
    yield from _serve(400, body)


def test_http_400_shows_the_rpc_message_and_the_archive_hint(pruned_node: str) -> None:
    with pytest.raises(RpcError) as info:
        RpcClient(endpoint=pruned_node, timeout=5).call("eth_getBlockByNumber", ["0x1", False])
    message = str(info.value)
    assert message.startswith("transport failure contacting http://127.0.0.1:")
    assert "HTTP Error 400" in message
    assert "RPC said: block is more than 10064 blocks behind head" in message
    assert "archive RPC" in message


def test_an_echoed_api_key_in_the_path_is_redacted() -> None:
    key = "/v2/abcdef0123456789SECRETKEY"
    body = json.dumps({"error": {"message": f"unknown key {key} for method"}}).encode()
    for base in _serve(403, body):
        with pytest.raises(RpcError) as info:
            RpcClient(endpoint=base + key, timeout=5).call("eth_chainId")
        message = str(info.value)
        assert "SECRETKEY" not in message
        assert "unknown key <redacted> for method" in message
        assert "archive" not in message


def test_a_plain_text_body_is_bounded_and_stripped_of_control_characters() -> None:
    body = b"rate limited\r\n\x1b[31m" + b"x" * 5000
    for base in _serve(429, body):
        with pytest.raises(RpcError) as info:
            RpcClient(endpoint=base, timeout=5).call("eth_chainId")
        message = str(info.value)
        assert "RPC said: rate limited" in message
        assert "\x1b" not in message and "\r" not in message
        assert message.endswith("…")
        assert len(message) < 600


def test_an_empty_body_keeps_the_plain_status() -> None:
    for base in _serve(400, b""):
        with pytest.raises(RpcError) as info:
            RpcClient(endpoint=base, timeout=5).call("eth_chainId")
        assert str(info.value).endswith("HTTP Error 400: Bad Request")
