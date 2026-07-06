"""Offline unit tests for TokenView ABI encoding/decoding (no chain).

These guard the hand-rolled calldata for ``transferWithAuthorization`` and the
read decoders, since the on-chain tests that depend on them only run on a dev
machine. A fake transport stands in for the chain.
"""

from __future__ import annotations

from typing import Any

from psv.anvil import RpcClient
from psv.chain import SEL_TRANSFER_WITH_AUTHORIZATION, TokenView

TOKEN = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
ADDR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


def _rpc_returning(result: str):
    def send(request: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request["id"], "result": result}

    return RpcClient(transport=send)


def test_balance_of_decodes_uint() -> None:
    token = TokenView(_rpc_returning("0x" + f"{12345:064x}"), TOKEN)
    assert token.balance_of(ADDR) == 12345


def test_authorization_used_truthy() -> None:
    token = TokenView(_rpc_returning("0x" + f"{1:064x}"), TOKEN)
    assert token.authorization_used(ADDR, "0x" + "ab" * 32) is True
    token0 = TokenView(_rpc_returning("0x" + f"{0:064x}"), TOKEN)
    assert token0.authorization_used(ADDR, "0x" + "ab" * 32) is False


def test_set_event_mode_calldata() -> None:
    token = TokenView(_rpc_returning("0x"), TOKEN)
    data = token.set_event_mode_calldata(1)
    assert data == "0x2a030f44" + f"{1:064x}"


def test_settle_calldata_structure() -> None:
    token = TokenView(_rpc_returning("0x"), TOKEN)
    sig = "0x" + "11" * 65  # 65-byte signature
    data = token.settle_calldata(
        from_addr=ADDR,
        to=TOKEN,
        value=10_000,
        valid_after=0,
        valid_before=2**48,
        nonce="0x" + "cd" * 32,
        signature=sig,
    )
    body = data.removeprefix("0x")
    assert body[:8] == SEL_TRANSFER_WITH_AUTHORIZATION
    # 7 head words after the selector, then the bytes offset must point past them.
    offset_word = body[8 + 6 * 64 : 8 + 7 * 64]
    assert int(offset_word, 16) == 7 * 32
    # bytes length prefix == 65, followed by the signature, right-padded to 32B.
    length_word = body[8 + 7 * 64 : 8 + 8 * 64]
    assert int(length_word, 16) == 65
    sig_region = body[8 + 8 * 64 :]
    assert sig_region.startswith("11" * 65)
    assert len(sig_region) % 64 == 0  # padded to a whole word
