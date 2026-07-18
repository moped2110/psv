"""Strict JSON-RPC client and process manager for a local Anvil chain.

The client deliberately treats the RPC endpoint as untrusted input.  Transport,
JSON decoding, envelope, and method-result failures all surface as :class:`RpcError`.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

Transport = Callable[[dict[str, Any]], object]

_MAX_RPC_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_RPC_LIST_ITEMS = 10_000
_MAX_RPC_OBJECT_FIELDS = 256
_MAX_RPC_STRING_CHARS = 2 * 1024 * 1024
_MAX_RPC_DEPTH = 16
_QUANTITY_RE = re.compile(r"^0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)$")
_DATA_RE = re.compile(r"^0x(?:[0-9a-fA-F]{2})*$")
_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_BLOCK_TAGS = frozenset({"earliest", "latest", "pending", "safe", "finalized"})


class RpcError(RuntimeError):
    """An untrusted RPC transport, envelope, error, or result failure."""


def _reject_json_constant(value: str) -> object:
    """Reject non-standard JSON constants such as NaN and Infinity."""
    raise ValueError(f"non-standard JSON constant {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build an object while rejecting duplicate JSON member names."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _bounded_json(value: object, *, what: str, depth: int = 0) -> None:
    """Reject resource-exhaustion shapes even for injected/custom transports."""
    if depth > _MAX_RPC_DEPTH:
        raise RpcError(f"{what}: JSON nesting exceeds {_MAX_RPC_DEPTH}")
    if isinstance(value, str):
        if len(value) > _MAX_RPC_STRING_CHARS:
            raise RpcError(f"{what}: string exceeds size limit")
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise RpcError(f"{what}: non-finite JSON number")
    if value is None or type(value) in {bool, int, float}:  # noqa: E721
        return
    if isinstance(value, list):
        if len(value) > _MAX_RPC_LIST_ITEMS:
            raise RpcError(f"{what}: list exceeds item limit")
        for item in value:
            _bounded_json(item, what=what, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > _MAX_RPC_OBJECT_FIELDS:
            raise RpcError(f"{what}: object exceeds field limit")
        for key, item in value.items():
            if not isinstance(key, str):
                raise RpcError(f"{what}: object key is not a string")
            _bounded_json(item, what=what, depth=depth + 1)
        return
    raise RpcError(f"{what}: unsupported JSON value {type(value).__name__}")


def _quantity(value: object, what: str) -> int:
    """Decode a canonical non-negative JSON-RPC hex quantity."""
    if not isinstance(value, str) or _QUANTITY_RE.fullmatch(value) is None:
        raise RpcError(f"{what}: expected canonical hex quantity, got {value!r}")
    return int(value, 16)


def _data(value: object, what: str) -> str:
    """Validate and return even-length 0x-prefixed hexadecimal data."""
    if not isinstance(value, str) or _DATA_RE.fullmatch(value) is None:
        raise RpcError(f"{what}: expected even-length hex data, got {value!r}")
    return value


def _hash(value: object, what: str) -> str:
    """Validate and return an exact 32-byte JSON-RPC hash."""
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise RpcError(f"{what}: expected a 32-byte hash, got {value!r}")
    return value


def _address(value: object, what: str, *, nullable: bool = False) -> str | None:
    """Validate and return an exact EVM address, optionally allowing null."""
    if value is None and nullable:
        return None
    if not isinstance(value, str) or _ADDRESS_RE.fullmatch(value) is None:
        raise RpcError(f"{what}: expected a 20-byte address, got {value!r}")
    return value


def _block_param(block: int | str) -> str:
    """Encode an integer or approved block tag for an RPC request."""
    if type(block) is int and block >= 0:
        return hex(block)
    if isinstance(block, str) and block in _BLOCK_TAGS:
        return block
    raise ValueError(f"invalid block selector: {block!r}")


def _validate_log(value: object, what: str) -> dict[str, Any]:
    """Validate the complete shape and scalar domains of an EVM log."""
    if not isinstance(value, dict):
        raise RpcError(f"{what}: expected log object, got {type(value).__name__}")
    required = {
        "address",
        "topics",
        "data",
        "blockNumber",
        "transactionHash",
        "transactionIndex",
        "blockHash",
        "logIndex",
        "removed",
    }
    missing = required.difference(value)
    if missing:
        raise RpcError(f"{what}: log missing fields {sorted(missing)!r}")
    _address(value["address"], f"{what}.address")
    topics = value["topics"]
    if not isinstance(topics, list) or len(topics) > 16:
        raise RpcError(f"{what}.topics: expected a bounded list")
    for index, topic in enumerate(topics):
        _hash(topic, f"{what}.topics[{index}]")
    _data(value["data"], f"{what}.data")
    _quantity(value["blockNumber"], f"{what}.blockNumber")
    _hash(value["transactionHash"], f"{what}.transactionHash")
    _quantity(value["transactionIndex"], f"{what}.transactionIndex")
    _hash(value["blockHash"], f"{what}.blockHash")
    _quantity(value["logIndex"], f"{what}.logIndex")
    if type(value["removed"]) is not bool:
        raise RpcError(f"{what}.removed: expected boolean")
    return dict(value)


def _validate_receipt(value: object, what: str) -> dict[str, Any]:
    """Validate a mined receipt and every log it contains."""
    if not isinstance(value, dict):
        raise RpcError(f"{what}: expected receipt object, got {type(value).__name__}")
    required = {"transactionHash", "blockHash", "blockNumber", "status", "logs"}
    missing = required.difference(value)
    if missing:
        raise RpcError(f"{what}: receipt missing fields {sorted(missing)!r}")
    _hash(value["transactionHash"], f"{what}.transactionHash")
    _hash(value["blockHash"], f"{what}.blockHash")
    _quantity(value["blockNumber"], f"{what}.blockNumber")
    status = _quantity(value["status"], f"{what}.status")
    if status not in {0, 1}:
        raise RpcError(f"{what}.status: expected 0 or 1")
    logs = value["logs"]
    if not isinstance(logs, list) or len(logs) > _MAX_RPC_LIST_ITEMS:
        raise RpcError(f"{what}.logs: expected a bounded list")
    for index, log in enumerate(logs):
        _validate_log(log, f"{what}.logs[{index}]")
    for key in ("transactionIndex", "cumulativeGasUsed", "gasUsed", "type"):
        if key in value:
            _quantity(value[key], f"{what}.{key}")
    if "from" in value:
        _address(value["from"], f"{what}.from")
    if "to" in value:
        _address(value["to"], f"{what}.to", nullable=True)
    return dict(value)


def _urllib_transport(endpoint: str, timeout: float) -> Transport:
    """Create a bounded HTTP transport for one JSON-RPC endpoint."""

    def send(request: dict[str, Any]) -> object:
        """POST one request and strictly decode its bounded JSON response."""
        data = json.dumps(request).encode()
        req = urllib.request.Request(
            endpoint,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "psv/0.1 (read-only JSON-RPC verifier)",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                raw = resp.read(_MAX_RPC_RESPONSE_BYTES + 1)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise RpcError(f"transport failure contacting {endpoint}: {exc}") from exc
        if len(raw) > _MAX_RPC_RESPONSE_BYTES:
            raise RpcError(f"response from {endpoint} exceeds size limit")
        try:
            return json.loads(
                raw.decode("utf-8"),
                parse_constant=_reject_json_constant,
                object_pairs_hook=_reject_duplicate_keys,
            )
        except (UnicodeError, ValueError) as exc:
            raise RpcError(f"malformed (non-JSON) response from {endpoint}: {exc}") from exc

    return send


@dataclass
class RpcClient:
    """Minimal JSON-RPC 2.0 client with strict, bounded responses."""

    endpoint: str = "http://127.0.0.1:8545"
    timeout: float = 10.0
    transport: Transport | None = None
    _id: int = field(default=0, init=False)

    def _send(self) -> Transport:
        """Return the injected transport or create the default HTTP transport."""
        return self.transport or _urllib_transport(self.endpoint, self.timeout)

    def call(self, method: str, params: list[Any] | None = None) -> Any:
        """Call one JSON-RPC method and validate its envelope before returning the result."""
        self._id += 1
        request_id = self._id
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or [],
        }
        try:
            response = self._send()(request)
        except RpcError:
            raise
        except Exception as exc:
            raise RpcError(f"{method}: transport failure: {exc}") from exc
        _bounded_json(response, what=f"{method} response")
        if not isinstance(response, dict):
            raise RpcError(f"{method}: response must be a JSON object")
        if response.get("jsonrpc") != "2.0":
            raise RpcError(f"{method}: response has invalid jsonrpc version")
        response_id = response.get("id")
        if type(response_id) is not int or response_id != request_id:
            raise RpcError(f"{method}: response id does not match request")
        has_result = "result" in response
        has_error = "error" in response
        if has_result == has_error:
            raise RpcError(f"{method}: response must contain exactly one of result/error")
        if has_error:
            error = response["error"]
            if not isinstance(error, dict):
                raise RpcError(f"{method}: malformed error object")
            code = error.get("code")
            message = error.get("message")
            if type(code) is not int or not isinstance(message, str):
                raise RpcError(f"{method}: malformed error object")
            raise RpcError(f"{method}: RPC error {code}: {message}")
        return response["result"]

    def snapshot(self) -> str:
        """Create an Anvil state snapshot and return its canonical identifier."""
        result = self.call("evm_snapshot")
        _quantity(result, "evm_snapshot result")
        return str(result)

    def revert(self, snapshot_id: str) -> bool:
        """Revert Anvil to a previously validated snapshot identifier."""
        _quantity(snapshot_id, "snapshot id")
        result = self.call("evm_revert", [snapshot_id])
        if type(result) is not bool:
            raise RpcError("evm_revert result: expected boolean")
        return result

    def mine(self, blocks: int = 1) -> None:
        """Mine a bounded positive number of blocks synchronously."""
        if type(blocks) is not int or not 1 <= blocks <= 1_000_000:
            raise ValueError("blocks must be an integer within [1, 1000000]")
        for _ in range(blocks):
            result = self.call("evm_mine")
            if result is not None:
                _quantity(result, "evm_mine result")

    def increase_time(self, seconds: int) -> None:
        """Advance Anvil time by non-negative seconds and mine the change."""
        if type(seconds) is not int or seconds < 0:
            raise ValueError("seconds must be a non-negative integer")
        result = self.call("evm_increaseTime", [seconds])
        _quantity(result, "evm_increaseTime result")
        self.mine()

    def set_automine(self, on: bool) -> None:
        """Enable or disable Anvil automining after validating the response."""
        if type(on) is not bool:
            raise ValueError("on must be a boolean")
        result = self.call("evm_setAutomine", [on])
        if result is not None and type(result) is not bool:
            raise RpcError("evm_setAutomine result: expected boolean or null")

    def block_number(self) -> int:
        """Return the latest chain height as an exact integer."""
        return _quantity(self.call("eth_blockNumber"), "eth_blockNumber result")

    def chain_id(self) -> int:
        """Return the live EVM chain id as a strictly decoded quantity."""
        chain_id = _quantity(self.call("eth_chainId"), "eth_chainId result")
        if chain_id <= 0:
            raise RpcError("eth_chainId result: chain id must be positive")
        return chain_id

    def get_block(
        self, block: int | str = "latest", *, full_transactions: bool = False
    ) -> dict[str, Any]:
        """Return one block suitable for pinning subsequent state reads."""
        if type(full_transactions) is not bool:
            raise ValueError("full_transactions must be a boolean")
        result = self.call("eth_getBlockByNumber", [_block_param(block), full_transactions])
        if not isinstance(result, dict):
            raise RpcError("eth_getBlockByNumber result: expected block object")
        for key in ("number", "timestamp"):
            if key not in result:
                raise RpcError(f"eth_getBlockByNumber result: missing {key}")
            _quantity(result[key], f"block.{key}")
        for key in ("hash", "parentHash"):
            if key not in result:
                raise RpcError(f"eth_getBlockByNumber result: missing {key}")
            _hash(result[key], f"block.{key}")
        txs = result.get("transactions")
        if not isinstance(txs, list) or len(txs) > _MAX_RPC_LIST_ITEMS:
            raise RpcError("block.transactions: expected a bounded list")
        if not full_transactions:
            for index, tx_hash in enumerate(txs):
                _hash(tx_hash, f"block.transactions[{index}]")
        elif any(not isinstance(tx, dict) for tx in txs):
            raise RpcError("block.transactions: expected transaction objects")
        return dict(result)

    def get_logs(
        self,
        *,
        address: str,
        topics: list[str | None],
        from_block: int | str = "earliest",
        to_block: int | str = "latest",
    ) -> list[dict[str, Any]]:
        """Fetch bounded logs for an exact address, topics, and block range."""
        _address(address, "eth_getLogs address")
        if not isinstance(topics, list) or len(topics) > 16:
            raise ValueError("topics must be a list with at most 16 entries")
        for topic in topics:
            if topic is not None:
                _hash(topic, "eth_getLogs topic")
        flt = {
            "address": address,
            "topics": topics,
            "fromBlock": _block_param(from_block),
            "toBlock": _block_param(to_block),
        }
        result = self.call("eth_getLogs", [flt])
        if not isinstance(result, list) or len(result) > _MAX_RPC_LIST_ITEMS:
            raise RpcError("eth_getLogs result: expected a bounded list")
        return [_validate_log(log, f"eth_getLogs result[{i}]") for i, log in enumerate(result)]

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        """Execute a read-only contract call at an explicit block selector."""
        _address(to, "eth_call to")
        _data(data, "eth_call data")
        return _data(
            self.call("eth_call", [{"to": to, "data": data}, _block_param(block)]),
            "eth_call result",
        )

    def get_code(self, address: str, block: int | str = "latest") -> str:
        """Return validated bytecode for an address at an explicit block."""
        _address(address, "eth_getCode address")
        return _data(self.call("eth_getCode", [address, _block_param(block)]), "eth_getCode result")

    def send_raw_transaction(self, raw: str) -> str:
        """Broadcast validated signed transaction bytes and return its hash."""
        _data(raw, "raw transaction")
        return _hash(self.call("eth_sendRawTransaction", [raw]), "eth_sendRawTransaction result")

    def get_transaction_receipt(self, tx_hash: str) -> dict[str, Any]:
        """Return a mined receipt, rejecting missing or malformed evidence."""
        _hash(tx_hash, "transaction hash")
        receipt = self.call("eth_getTransactionReceipt", [tx_hash])
        if receipt is None:
            raise RpcError(f"no receipt for {tx_hash}")
        return _validate_receipt(receipt, "eth_getTransactionReceipt result")

    def wait_for_receipt(
        self, tx_hash: str, *, tries: int = 50, delay: float = 0.1
    ) -> dict[str, Any]:
        """Poll for a mined receipt using bounded attempts and delay."""
        _hash(tx_hash, "transaction hash")
        if type(tries) is not int or tries <= 0:
            raise ValueError("tries must be a positive integer")
        if not isinstance(delay, (int, float)) or isinstance(delay, bool) or delay < 0:
            raise ValueError("delay must be non-negative")
        for _ in range(tries):
            receipt = self.call("eth_getTransactionReceipt", [tx_hash])
            if receipt is not None:
                return _validate_receipt(receipt, "eth_getTransactionReceipt result")
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
        """Return the loopback HTTP endpoint managed by this process."""
        return f"http://{self.host}:{self.port}"

    def start(self, *, ready_timeout: float = 15.0) -> AnvilProcess:
        """Start Anvil and wait until its JSON-RPC endpoint responds."""
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
        """Create a strict client bound to the managed Anvil endpoint."""
        return RpcClient(endpoint=self.endpoint)

    def stop(self) -> None:
        """Terminate the managed process, escalating to kill after a timeout."""
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None

    def __enter__(self) -> AnvilProcess:
        """Start Anvil when entering a context manager."""
        return self.start()

    def __exit__(self, *exc: object) -> None:
        """Always stop Anvil when leaving a context manager."""
        self.stop()
