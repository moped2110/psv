"""Strict synthetic JSON-RPC evidence shared by reconciliation unit tests."""

from __future__ import annotations

from typing import Any

from psv.anvil import RpcClient
from psv.chain import TOPIC_AUTHORIZATION_USED, TokenView
from psv.rails import RailConfig
from psv.reconciliation import TOPIC_TRANSFER, topic_addr

PAYER = "0x" + "11" * 20
PAYEE = "0x" + "22" * 20
NONCE = "0x" + "ab" * 32
TX_HASH = "0x" + "cd" * 32
BLOCK_HASH = "0x" + "10" * 32
FINALITY_HASH = "0x" + "12" * 32
PARENT_HASH = "0x" + "09" * 32


def transfer_log(
    *,
    token: str,
    value: int,
    tx_hash: str = TX_HASH,
    log_index: int = 0,
    block_hash: str = BLOCK_HASH,
    block_number: int = 10,
    payer: str = PAYER,
    payee: str = PAYEE,
    removed: bool = False,
) -> dict[str, object]:
    return {
        "address": token,
        "topics": [TOPIC_TRANSFER, topic_addr(payer), topic_addr(payee)],
        "data": "0x" + f"{value:064x}",
        "blockNumber": hex(block_number),
        "transactionHash": tx_hash,
        "transactionIndex": "0x0",
        "blockHash": block_hash,
        "logIndex": hex(log_index),
        "removed": removed,
    }


def authorization_log(
    *, token: str, tx_hash: str = TX_HASH, block_hash: str = BLOCK_HASH
) -> dict[str, object]:
    return {
        "address": token,
        "topics": [TOPIC_AUTHORIZATION_USED, topic_addr(PAYER), NONCE],
        "data": "0x",
        "blockNumber": "0xa",
        "transactionHash": tx_hash,
        "transactionIndex": "0x0",
        "blockHash": block_hash,
        "logIndex": "0x1",
        "removed": False,
    }


def strict_token(
    rail: RailConfig,
    *,
    payer_before: int = 1000,
    payer_after: int = 900,
    payee_before: int = 0,
    payee_after: int = 100,
    event_value: int = 100,
    receipt_status: int = 1,
    nonce_used: bool | None = None,
    live_chain_id: int | None = None,
    extra_same_block_log: dict[str, object] | None = None,
    removed: bool = False,
    reorg_on_recheck: bool = False,
    code: str = "0x6000",
) -> TokenView:
    transfer = transfer_log(token=rail.token_address, value=event_value, removed=removed)
    receipt_logs = [transfer, authorization_log(token=rail.token_address)] if receipt_status else []
    receipt_calls = 0

    def response(request: dict[str, Any], result: object) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request["id"], "result": result}

    def transport(request: dict[str, Any]) -> dict[str, Any]:
        nonlocal receipt_calls
        method = request["method"]
        params = request["params"]
        if method == "eth_chainId":
            return response(request, hex(live_chain_id or rail.chain_id))
        if method == "eth_getBlockByNumber":
            selector = params[0]
            if selector in {"latest", "safe", "finalized"}:
                number, block_hash, parent = 12, FINALITY_HASH, BLOCK_HASH
            else:
                number = int(selector, 16)
                block_hash = BLOCK_HASH if number == 10 else PARENT_HASH
                parent = PARENT_HASH if number == 10 else "0x" + "08" * 32
                if reorg_on_recheck and number == 10 and receipt_calls >= 2:
                    block_hash = "0x" + "ff" * 32
            block = {
                "number": hex(number),
                "timestamp": "0x1",
                "hash": block_hash,
                "parentHash": parent,
                "transactions": [TX_HASH] if number == 10 else [],
            }
            return response(request, block)
        if method == "eth_getTransactionReceipt":
            receipt_calls += 1
            block_hash = "0x" + "ff" * 32 if reorg_on_recheck and receipt_calls >= 2 else BLOCK_HASH
            receipt = {
                "transactionHash": TX_HASH,
                "blockHash": block_hash,
                "blockNumber": "0xa",
                "status": hex(receipt_status),
                "logs": receipt_logs,
            }
            return response(request, receipt)
        if method == "eth_getCode":
            return response(request, code)
        if method == "eth_call":
            call, block = params
            selector = call["data"][2:10]
            who = "0x" + call["data"][-40:]
            at_parent = block == "0x9"
            if selector == "70a08231":
                if who.lower() == PAYER:
                    value = payer_before if at_parent else payer_after
                else:
                    value = payee_before if at_parent else payee_after
            elif selector == "e94a0102":
                value = int(bool(receipt_status) if nonce_used is None else nonce_used)
            else:
                value = 0
            return response(request, "0x" + f"{value:064x}")
        if method == "eth_getLogs":
            flt = params[0]
            wanted = flt["topics"]
            logs: list[dict[str, object]] = [] if receipt_status == 0 else [transfer]
            if extra_same_block_log is not None:
                logs.append(extra_same_block_log)

            def matches(log: dict[str, object]) -> bool:
                topics = log["topics"]
                assert isinstance(topics, list)
                return all(want is None or want == topics[i] for i, want in enumerate(wanted))

            return response(request, [log for log in logs if matches(log)])
        raise AssertionError(f"unexpected RPC method: {method}")

    return TokenView(RpcClient(transport=transport), rail.token_address)
