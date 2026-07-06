"""Offline unit tests filling remaining gaps in chain.TokenView and sut parsers."""

from __future__ import annotations

from typing import Any

from psv.chain import SEL_MINT, TOPIC_AUTHORIZATION_USED, TokenView
from psv.sut import parse_pay, parse_quote, parse_status

TOKEN = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
ADDR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


class _Rpc:
    """Records eth_call/get_logs args and returns a canned scalar."""

    def __init__(self, result: str = "0x0") -> None:
        self.result = result
        self.calls: list[Any] = []

    def eth_call(self, to: str, data: str, block: str = "latest") -> str:
        self.calls.append(("eth_call", to, data))
        return self.result

    def get_logs(self, *, address: str, topics: list[Any], from_block: Any) -> list[dict[str, Any]]:
        self.calls.append(("get_logs", address, topics, from_block))
        return []


def test_event_mode_decodes_int() -> None:
    token = TokenView(_Rpc("0x" + f"{1:064x}"), TOKEN)  # type: ignore[arg-type]
    assert token.event_mode() == 1


def test_authorization_used_logs_builds_topics() -> None:
    rpc = _Rpc()
    token = TokenView(rpc, TOKEN)  # type: ignore[arg-type]
    token.authorization_used_logs(authorizer=ADDR, from_block=7)
    kind, address, topics, from_block = rpc.calls[0]
    assert kind == "get_logs" and address == TOKEN and from_block == 7
    assert topics[0] == TOPIC_AUTHORIZATION_USED
    assert topics[1] == "0x" + ADDR.lower().removeprefix("0x").rjust(64, "0")


def test_authorization_used_logs_without_authorizer_is_wildcard() -> None:
    rpc = _Rpc()
    TokenView(rpc, TOKEN).authorization_used_logs()  # type: ignore[arg-type]
    _, _, topics, _ = rpc.calls[0]
    assert topics == [TOPIC_AUTHORIZATION_USED, None]


def test_mint_calldata() -> None:
    data = TokenView(_Rpc(), TOKEN).mint_calldata(ADDR, 1_000_000)  # type: ignore[arg-type]
    body = data.removeprefix("0x")
    assert body[:8] == SEL_MINT
    assert body[8 : 8 + 64] == ADDR.lower().removeprefix("0x").rjust(64, "0")
    assert int(body[8 + 64 : 8 + 128], 16) == 1_000_000


def test_parse_quote_chain_id() -> None:
    q = parse_quote(
        {
            "order_id": "o",
            "amount": "1",
            "payTo": "0xm",
            "asset": "0xt",
            "network": "eip155:8453",
            "extra": {"name": "USDC", "version": "2"},
        }
    )
    assert q.chain_id == 8453


def test_parse_pay_and_status() -> None:
    p = parse_pay({"order_id": "o", "submitted_tx": "0xtx", "settled": True})
    assert p.settled is True and p.submitted_tx == "0xtx"
    s = parse_status({"order_id": "o", "paid": True, "resource": "premium", "submitted_tx": "0xtx"})
    assert s.paid is True and s.resource == "premium"
    # tolerant defaults when fields are missing
    s2 = parse_status({"order_id": "o"})
    assert s2.paid is False and s2.resource is None
