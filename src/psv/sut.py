"""The SUT adapter contract: how the harness talks to *any* payment system.

Tests speak to the System-under-Test only through this interface — never its
internals — so the same test suite runs against the bundled reference SUT, a
future system of Mario's, or a third-party implementation. The contract is
deliberately tiny: quote, pay, status.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class Quote:
    """What the SUT asks to be paid: a normalized x402 ``accepts`` entry."""

    order_id: str
    amount: int
    pay_to: str
    asset: str  # token contract address
    network: str  # CAIP-2, e.g. eip155:84532
    token_name: str
    token_version: str

    @property
    def chain_id(self) -> int:
        return int(self.network.split(":", 1)[1])


@dataclass
class PayResult:
    order_id: str
    submitted_tx: str | None
    settled: bool  # what the SUT *believes* about settlement


@dataclass
class Status:
    order_id: str
    paid: bool
    resource: str | None
    submitted_tx: str | None


class SutAdapter(ABC):
    """Minimal HTTP contract every System-under-Test must satisfy."""

    @abstractmethod
    def quote(self) -> Quote: ...

    @abstractmethod
    def pay(self, order_id: str, authorization: dict[str, Any]) -> PayResult: ...

    @abstractmethod
    def status(self, order_id: str) -> Status: ...


def parse_quote(body: dict[str, Any]) -> Quote:
    extra = body.get("extra") or {}
    return Quote(
        order_id=str(body["order_id"]),
        amount=int(body["amount"]),
        pay_to=str(body["payTo"]),
        asset=str(body["asset"]),
        network=str(body["network"]),
        token_name=str(extra.get("name", "USDC")),
        token_version=str(extra.get("version", "2")),
    )


def parse_pay(body: dict[str, Any]) -> PayResult:
    return PayResult(
        order_id=str(body["order_id"]),
        submitted_tx=body.get("submitted_tx"),
        settled=bool(body.get("settled", False)),
    )


def parse_status(body: dict[str, Any]) -> Status:
    return Status(
        order_id=str(body["order_id"]),
        paid=bool(body.get("paid", False)),
        resource=body.get("resource"),
        submitted_tx=body.get("submitted_tx"),
    )


@dataclass
class HttpSutAdapter(SutAdapter):
    """Drives a SUT over its HTTP endpoints (``/quote``, ``/pay``, ``/status``)."""

    base_url: str
    timeout: float = 30.0
    _client: Any = None  # httpx.Client, lazily created or injected for tests

    def _http(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.Client(base_url=self.base_url, timeout=self.timeout)
        return self._client

    def quote(self) -> Quote:
        resp = self._http().post("/quote")
        resp.raise_for_status()
        return parse_quote(resp.json())

    def pay(self, order_id: str, authorization: dict[str, Any]) -> PayResult:
        resp = self._http().post(
            "/pay", json={"order_id": order_id, "authorization": authorization}
        )
        resp.raise_for_status()
        return parse_pay(resp.json())

    def status(self, order_id: str) -> Status:
        resp = self._http().get(f"/status/{order_id}")
        resp.raise_for_status()
        return parse_status(resp.json())
