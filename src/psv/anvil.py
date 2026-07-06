"""Anvil control: a thin JSON-RPC client + process manager for a local EVM chain.

The harness needs a chain it can *manipulate*, not just read: snapshot/revert per
test, mine on demand, advance time, and replay/reorg. Anvil (Foundry) gives all
of that deterministically with no cloud billing.

Design for offline testability: ``RpcClient`` is built around an injectable
``send`` callable, so the JSON-RPC request construction and response/error
handling can be unit-tested with a fake transport — no chain required. The
default transport uses stdlib ``urllib`` (no third-party dependency).
``AnvilProcess`` manages the ``anvil`` subprocess and is only exercised on a dev
machine (the ``onchain`` test marker), never in the CI sandbox.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# A transport: given a fully-formed JSON-RPC request object, return the parsed
# JSON-RPC response object. Injectable so tests can supply a canned chain.
Transport = Callable[[dict[str, Any]], dict[str, Any]]


class RpcError(RuntimeError):
    """A JSON-RPC error response (``{"error": {...}}``) or a transport failure."""


def _urllib_transport(endpoint: str, timeout: float) -> Transport:
    def send(request: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(request).encode()
        req = urllib.request.Request(
            endpoint, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (local Anvil only)
            body: dict[str, Any] = json.loads(resp.read().decode())
            return body

    return send


@dataclass
class RpcClient:
    """Minimal JSON-RPC 2.0 client with monotonic ids and error raising."""

    endpoint: str = "http://127.0.0.1:8545"
    timeout: float = 10.0
    transport: Transport | None = None
    _id: int = field(default=0, init=False)

    def _send(self) -> Transport:
        return self.transport or _urllib_transport(self.endpoint, self.timeout)

    def call(self, method: str, params: list[Any] | None = None) -> Any:
        self._id += 1
        request = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or []}
        response = self._send()(request)
        if "error" in response and response["error"] is not None:
            raise RpcError(f"{method}: {response['error']}")
        if "result" not in response:
            raise RpcError(f"{method}: malformed response (no result): {response!r}")
        return response["result"]

    # --- chain manipulation (the reason we use Anvil) -------------------------

    def snapshot(self) -> str:
        """Take a snapshot; returns an id usable with :meth:`revert`."""
        return str(self.call("evm_snapshot"))

    def revert(self, snapshot_id: str) -> bool:
        """Revert chain state to a snapshot. Anvil invalidates later snapshots."""
        return bool(self.call("evm_revert", [snapshot_id]))

    def mine(self, blocks: int = 1) -> None:
        for _ in range(blocks):
            self.call("evm_mine")

    def increase_time(self, seconds: int) -> None:
        self.call("evm_increaseTime", [seconds])
        self.call("evm_mine")

    def set_automine(self, on: bool) -> None:
        self.call("evm_setAutomine", [on])

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber"), 16)

    # --- reads ----------------------------------------------------------------

    def get_logs(
        self,
        *,
        address: str,
        topics: list[str | None],
        from_block: int | str = "earliest",
        to_block: int | str = "latest",
    ) -> list[dict[str, Any]]:
        """``eth_getLogs`` filtered by contract address + topic list.

        ``topics[0]`` is the event signature hash (topic0). A confirmer that scans
        for the wrong topic0 — e.g. after an ABI drift — gets back an empty list.
        """

        def enc(b: int | str) -> str:
            return b if isinstance(b, str) else hex(b)

        flt = {
            "address": address,
            "topics": topics,
            "fromBlock": enc(from_block),
            "toBlock": enc(to_block),
        }
        result: list[dict[str, Any]] = self.call("eth_getLogs", [flt])
        return result

    def eth_call(self, to: str, data: str, block: str = "latest") -> str:
        return str(self.call("eth_call", [{"to": to, "data": data}, block]))

    def get_code(self, address: str, block: str = "latest") -> str:
        """``eth_getCode``: deployed bytecode at ``address`` (``"0x"`` for an EOA).

        The pre-flight that distinguishes a real token from a wallet address: an
        EOA has no code, so a settlement against it is a silent no-op (x402#2554).
        """
        return str(self.call("eth_getCode", [address, block]))

    def send_raw_transaction(self, raw: str) -> str:
        return str(self.call("eth_sendRawTransaction", [raw]))

    def wait_for_receipt(
        self, tx_hash: str, *, tries: int = 50, delay: float = 0.1
    ) -> dict[str, Any]:
        for _ in range(tries):
            receipt = self.call("eth_getTransactionReceipt", [tx_hash])
            if receipt is not None:
                return dict(receipt)
            time.sleep(delay)
        raise RpcError(f"no receipt for {tx_hash} after {tries} tries")


@dataclass
class AnvilProcess:
    """Spawn and manage a local ``anvil`` instance (dev machine only)."""

    chain_id: int = 84532
    port: int = 8545
    host: str = "127.0.0.1"
    extra_args: list[str] = field(default_factory=list)
    _proc: subprocess.Popen[bytes] | None = field(default=None, init=False)

    @property
    def endpoint(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self, *, ready_timeout: float = 15.0) -> AnvilProcess:
        cmd = [
            "anvil",
            "--chain-id",
            str(self.chain_id),
            "--port",
            str(self.port),
            "--host",
            self.host,
            *self.extra_args,
        ]
        self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        client = RpcClient(endpoint=self.endpoint, timeout=1.0)
        deadline = time.time() + ready_timeout
        while time.time() < deadline:
            try:
                client.block_number()
                return self
            except Exception:
                time.sleep(0.2)
        self.stop()
        raise RuntimeError(f"anvil did not become ready on {self.endpoint}")

    def client(self) -> RpcClient:
        return RpcClient(endpoint=self.endpoint)

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None

    def __enter__(self) -> AnvilProcess:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
