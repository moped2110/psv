"""Offline unit tests for the remaining RpcClient chain-control methods.

A fake transport records the JSON-RPC requests, so we verify the exact method
names and params without a chain.
"""

from __future__ import annotations

from typing import Any

import pytest

from psv.anvil import RpcClient, RpcError


def recording_transport(results: dict[str, Any]):
    seen: list[dict[str, Any]] = []

    def send(request: dict[str, Any]) -> dict[str, Any]:
        seen.append(request)
        return {"jsonrpc": "2.0", "id": request["id"], "result": results.get(request["method"])}

    return send, seen


def test_mine_issues_n_evm_mine() -> None:
    send, seen = recording_transport({"evm_mine": "0x0"})
    RpcClient(transport=send).mine(3)
    assert [r["method"] for r in seen] == ["evm_mine", "evm_mine", "evm_mine"]


def test_increase_time_then_mines() -> None:
    send, seen = recording_transport({"evm_increaseTime": "0x0", "evm_mine": "0x0"})
    RpcClient(transport=send).increase_time(3600)
    assert [r["method"] for r in seen] == ["evm_increaseTime", "evm_mine"]
    assert seen[0]["params"] == [3600]


def test_set_automine_passes_bool() -> None:
    send, seen = recording_transport({"evm_setAutomine": True})
    RpcClient(transport=send).set_automine(False)
    assert seen[0]["method"] == "evm_setAutomine"
    assert seen[0]["params"] == [False]


def test_eth_call_shape() -> None:
    address = "0x" + "11" * 20
    send, seen = recording_transport({"eth_call": "0x2a"})
    out = RpcClient(transport=send).eth_call(address, "0xdead")
    assert out == "0x2a"
    assert seen[0]["params"] == [{"to": address, "data": "0xdead"}, "latest"]


def test_send_raw_transaction_returns_hash() -> None:
    tx_hash = "0x" + "ab" * 32
    send, _ = recording_transport({"eth_sendRawTransaction": tx_hash})
    assert RpcClient(transport=send).send_raw_transaction("0xf86b") == tx_hash


def test_wait_for_receipt_polls_until_present() -> None:
    # First poll: no receipt (None); second: a receipt.
    state = {"calls": 0}

    def send(request: dict[str, Any]) -> dict[str, Any]:
        if request["method"] == "eth_getTransactionReceipt":
            state["calls"] += 1
            result = (
                None
                if state["calls"] < 2
                else {
                    "transactionHash": "0x" + "ab" * 32,
                    "blockHash": "0x" + "cd" * 32,
                    "blockNumber": "0x5",
                    "status": "0x1",
                    "logs": [],
                }
            )
            return {"jsonrpc": "2.0", "id": request["id"], "result": result}
        return {"jsonrpc": "2.0", "id": request["id"], "result": None}

    receipt = RpcClient(transport=send).wait_for_receipt("0x" + "ab" * 32, tries=5, delay=0)
    assert receipt["status"] == "0x1"
    assert state["calls"] == 2


def test_wait_for_receipt_times_out() -> None:
    def send(request: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request["id"], "result": None}

    with pytest.raises(RpcError):
        RpcClient(transport=send).wait_for_receipt("0x" + "ab" * 32, tries=3, delay=0)
