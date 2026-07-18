"""Hostile JSON-RPC response tests for PSV-AUD-008."""

from __future__ import annotations

from typing import Any

import pytest

from psv.anvil import RpcClient, RpcError

ADDRESS = "0x" + "11" * 20
TX_HASH = "0x" + "ab" * 32
BLOCK_HASH = "0x" + "cd" * 32


def _rpc_result(result: object) -> RpcClient:
    def transport(request: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request["id"], "result": result}

    return RpcClient(transport=transport)


@pytest.mark.parametrize(
    "response",
    [
        "result",
        [],
        {},
        {"jsonrpc": "1.0", "id": 1, "result": "0x1"},
        {"jsonrpc": "2.0", "id": 2, "result": "0x1"},
        {"jsonrpc": "2.0", "id": True, "result": "0x1"},
        {"jsonrpc": "2.0", "id": 1},
        {"jsonrpc": "2.0", "id": 1, "result": "0x1", "error": None},
        {"jsonrpc": "2.0", "id": 1, "result": "0x1", "error": {"code": -1, "message": "x"}},
        {"jsonrpc": "2.0", "id": 1, "error": "bad"},
        {"jsonrpc": "2.0", "id": 1, "error": {"code": "-1", "message": "bad"}},
    ],
)
def test_call_rejects_hostile_envelopes(response: object) -> None:
    with pytest.raises(RpcError):
        RpcClient(transport=lambda _request: response).call("eth_chainId")


def test_call_normalizes_injected_transport_exception() -> None:
    def transport(_request: dict[str, Any]) -> object:
        raise TypeError("hostile transport")

    with pytest.raises(RpcError, match="transport failure"):
        RpcClient(transport=transport).call("eth_chainId")


def test_call_rejects_nonfinite_json_number() -> None:
    with pytest.raises(RpcError, match="non-finite"):
        _rpc_result(float("nan")).call("test")


@pytest.mark.parametrize("value", [None, True, 1, "1", "0x", "0x00", "0xgg"])
def test_quantity_methods_reject_wrong_types_and_noncanonical_hex(value: object) -> None:
    with pytest.raises(RpcError):
        _rpc_result(value).block_number()


def test_chain_id_and_block_pinning_api() -> None:
    assert _rpc_result("0x14a34").chain_id() == 84532
    block = {
        "number": "0x5",
        "timestamp": "0x10",
        "hash": BLOCK_HASH,
        "parentHash": "0x" + "ef" * 32,
        "transactions": [TX_HASH],
    }
    assert _rpc_result(block).get_block(5)["hash"] == BLOCK_HASH


@pytest.mark.parametrize(
    "result",
    [
        [],
        {"number": "0x1", "timestamp": "0x1", "hash": BLOCK_HASH},
        {
            "number": "0x1",
            "timestamp": "0x1",
            "hash": "0x1",
            "parentHash": BLOCK_HASH,
            "transactions": [],
        },
        {
            "number": "0x1",
            "timestamp": "0x1",
            "hash": BLOCK_HASH,
            "parentHash": BLOCK_HASH,
            "transactions": "not-list",
        },
    ],
)
def test_get_block_rejects_hostile_shapes(result: object) -> None:
    with pytest.raises(RpcError):
        _rpc_result(result).get_block()


def _valid_log() -> dict[str, object]:
    return {
        "address": ADDRESS,
        "topics": [TX_HASH],
        "data": "0x",
        "blockNumber": "0x5",
        "transactionHash": TX_HASH,
        "transactionIndex": "0x0",
        "blockHash": BLOCK_HASH,
        "logIndex": "0x0",
        "removed": False,
    }


@pytest.mark.parametrize(
    "result", ["logs", {}, ["log"], [{}], [dict(_valid_log(), removed="false")]]
)
def test_get_logs_rejects_hostile_shapes(result: object) -> None:
    with pytest.raises(RpcError):
        _rpc_result(result).get_logs(address=ADDRESS, topics=[TX_HASH])


def _valid_receipt() -> dict[str, object]:
    return {
        "transactionHash": TX_HASH,
        "blockHash": BLOCK_HASH,
        "blockNumber": "0x5",
        "status": "0x1",
        "logs": [_valid_log()],
    }


def test_get_transaction_receipt_validates_hash_and_shape() -> None:
    assert _rpc_result(_valid_receipt()).get_transaction_receipt(TX_HASH)["status"] == "0x1"
    for result in (None, "receipt", {}, dict(_valid_receipt(), status="0x2")):
        with pytest.raises(RpcError):
            _rpc_result(result).get_transaction_receipt(TX_HASH)
    with pytest.raises(RpcError):
        _rpc_result(_valid_receipt()).get_transaction_receipt("0x1")


@pytest.mark.parametrize(
    ("method", "result"),
    [
        ("snapshot", "snapshot"),
        ("revert", "false"),
        ("eth_call", "0x1"),
        ("get_code", "garbage"),
        ("send", "0x1"),
    ],
)
def test_each_rpc_wrapper_rejects_bad_result_type(method: str, result: object) -> None:
    rpc = _rpc_result(result)
    with pytest.raises(RpcError):
        if method == "snapshot":
            rpc.snapshot()
        elif method == "revert":
            rpc.revert("0x1")
        elif method == "eth_call":
            rpc.eth_call(ADDRESS, "0x")
        elif method == "get_code":
            rpc.get_code(ADDRESS)
        else:
            rpc.send_raw_transaction("0x12")


def test_rpc_caps_oversized_lists_and_objects() -> None:
    with pytest.raises(RpcError, match="item limit"):
        _rpc_result([None] * 10_001).call("test")
    with pytest.raises(RpcError, match="field limit"):
        _rpc_result({str(index): index for index in range(257)}).call("test")
