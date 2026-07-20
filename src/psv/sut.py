"""Strict adapter contract for a system under test (SUT)."""

from __future__ import annotations

import json
import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, final
from urllib.parse import quote as quote_path_segment

_UINT256_MAX = 2**256 - 1
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_TX_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_NETWORK_RE = re.compile(r"^eip155:([1-9][0-9]*)$")
_AMOUNT_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")
_MAX_ORDER_ID_CHARS = 256
_MAX_TEXT_CHARS = 16_384
_MAX_RESPONSE_BYTES = 1024 * 1024


@final
class SutAdapterError(ValueError):
    """The remote SUT violated the adapter's HTTP or JSON wire contract."""


@dataclass(frozen=True)
@final
class Quote:
    """A normalized, validated x402 ``accepts`` entry."""

    order_id: str
    amount: int
    pay_to: str
    asset: str
    network: str
    token_name: str
    token_version: str

    @property
    def chain_id(self) -> int:
        """Extract the positive EVM chain identifier from the CAIP-2 network."""
        match = _NETWORK_RE.fullmatch(self.network)
        if match is None:  # construction outside parse_quote is still fail-closed
            raise SutAdapterError(f"invalid CAIP-2 EVM network: {self.network!r}")
        return int(match.group(1))


@dataclass(frozen=True)
@final
class PayResult:
    """Normalized result of submitting an authorization for an order."""

    order_id: str
    submitted_tx: str | None
    settled: bool


@dataclass(frozen=True)
@final
class Status:
    """Normalized read-only payment and resource state for an order."""

    order_id: str
    paid: bool
    resource: str | None
    submitted_tx: str | None


@final
class SutAdapter(ABC):
    """Minimal HTTP contract every System-under-Test must satisfy."""

    @abstractmethod
    def quote(self) -> Quote:
        """Request and normalize one payment quote from the SUT."""
        ...

    @abstractmethod
    def pay(self, order_id: str, authorization: dict[str, Any]) -> PayResult:
        """Submit one authorization for an exact order identifier."""
        ...

    @abstractmethod
    def status(self, order_id: str) -> Status:
        """Read normalized state for an exact order identifier."""
        ...


def _body_object(body: object, what: str) -> dict[str, Any]:
    """Require a JSON object whose member names are all strings."""
    if not isinstance(body, dict) or any(not isinstance(key, str) for key in body):
        raise SutAdapterError(f"{what}: response must be a JSON object with string keys")
    return body


def _required(body: dict[str, Any], field: str, what: str) -> Any:
    """Return a required wire field or raise a contract error."""
    if field not in body:
        raise SutAdapterError(f"{what}: missing required field {field!r}")
    return body[field]


def _wire_string(value: object, field: str, *, nullable: bool = False) -> str | None:
    """Validate a bounded non-empty wire string, optionally allowing null."""
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise SutAdapterError(f"{field}: expected string{' or null' if nullable else ''}")
    if not value or len(value) > _MAX_TEXT_CHARS:
        raise SutAdapterError(f"{field}: string is empty or exceeds the size limit")
    return value


def _order_id(value: object, field: str = "order_id") -> str:
    """Validate an order identifier safe for matching and URL encoding."""
    order_id = _wire_string(value, field)
    assert order_id is not None
    if len(order_id) > _MAX_ORDER_ID_CHARS or any(ord(char) < 0x20 for char in order_id):
        raise SutAdapterError(f"{field}: invalid order id")
    return order_id


def _address(value: object, field: str) -> str:
    """Require an exact 20-byte EVM address wire value."""
    if not isinstance(value, str) or _ADDRESS_RE.fullmatch(value) is None:
        raise SutAdapterError(f"{field}: expected exact 20-byte EVM address")
    return value


def _transaction_hash(value: object, field: str) -> str | None:
    """Require an exact transaction hash or null wire value."""
    if value is None:
        return None
    if not isinstance(value, str) or _TX_HASH_RE.fullmatch(value) is None:
        raise SutAdapterError(f"{field}: expected exact 32-byte transaction hash or null")
    return value


def _boolean(value: object, field: str) -> bool:
    """Require a literal JSON boolean without truthy coercion."""
    if type(value) is not bool:
        raise SutAdapterError(f"{field}: expected boolean")
    return value


def _positive_uint256(value: object, field: str) -> int:
    """Decode a canonical positive decimal-string uint256."""
    # x402 amounts are decimal strings on the wire; accepting numbers would lose
    # interoperability (and JSON numbers may already have passed through float).
    if not isinstance(value, str) or _AMOUNT_RE.fullmatch(value) is None:
        raise SutAdapterError(f"{field}: expected canonical decimal uint256 string")
    parsed = int(value)
    if not 1 <= parsed <= _UINT256_MAX:
        raise SutAdapterError(f"{field}: expected positive uint256")
    return parsed


def _network(value: object) -> str:
    """Validate a canonical positive EVM CAIP-2 network identifier."""
    if not isinstance(value, str):
        raise SutAdapterError("network: expected CAIP-2 string")
    match = _NETWORK_RE.fullmatch(value)
    if match is None or int(match.group(1)) > _UINT256_MAX:
        raise SutAdapterError("network: expected canonical eip155:<positive uint256>")
    return value


def _matching_order_id(body: dict[str, Any], what: str, expected_order_id: str | None) -> str:
    """Parse a response order id and bind it to the request when provided."""
    result = _order_id(_required(body, "order_id", what))
    if expected_order_id is not None and result != expected_order_id:
        raise SutAdapterError(
            f"{what}: response order_id {result!r} does not match request {expected_order_id!r}"
        )
    return result


def parse_quote(body: object) -> Quote:
    """Strictly parse a quote response into its normalized domain model."""
    parsed = _body_object(body, "quote")
    extra = _body_object(_required(parsed, "extra", "quote"), "quote.extra")
    name = _wire_string(_required(extra, "name", "quote.extra"), "extra.name")
    version = _wire_string(_required(extra, "version", "quote.extra"), "extra.version")
    assert name is not None and version is not None
    return Quote(
        order_id=_matching_order_id(parsed, "quote", None),
        amount=_positive_uint256(_required(parsed, "amount", "quote"), "amount"),
        pay_to=_address(_required(parsed, "payTo", "quote"), "payTo"),
        asset=_address(_required(parsed, "asset", "quote"), "asset"),
        network=_network(_required(parsed, "network", "quote")),
        token_name=name,
        token_version=version,
    )


def parse_pay(body: object, *, expected_order_id: str | None = None) -> PayResult:
    """Strictly parse a pay response and optionally bind its order id."""
    parsed = _body_object(body, "pay")
    return PayResult(
        order_id=_matching_order_id(parsed, "pay", expected_order_id),
        submitted_tx=_transaction_hash(parsed.get("submitted_tx"), "submitted_tx"),
        settled=_boolean(_required(parsed, "settled", "pay"), "settled"),
    )


def parse_status(body: object, *, expected_order_id: str | None = None) -> Status:
    """Strictly parse a status response and optionally bind its order id."""
    parsed = _body_object(body, "status")
    resource = _wire_string(_required(parsed, "resource", "status"), "resource", nullable=True)
    return Status(
        order_id=_matching_order_id(parsed, "status", expected_order_id),
        paid=_boolean(_required(parsed, "paid", "status"), "paid"),
        resource=resource,
        submitted_tx=_transaction_hash(_required(parsed, "submitted_tx", "status"), "submitted_tx"),
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Decode a JSON object while rejecting duplicate member names."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SutAdapterError(f"duplicate JSON field: {key!r}")
        result[key] = value
    return result


def _decode_response(response: Any, what: str) -> dict[str, Any]:
    """Read, bound, strictly decode, and validate an HTTP JSON response."""
    try:
        content = bytes(response.content)
    except Exception as exc:
        raise SutAdapterError(f"{what}: unable to read HTTP response: {exc}") from exc
    if len(content) > _MAX_RESPONSE_BYTES:
        raise SutAdapterError(f"{what}: response exceeds {_MAX_RESPONSE_BYTES} bytes")
    try:
        value = json.loads(content.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except SutAdapterError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise SutAdapterError(f"{what}: malformed JSON response: {exc}") from exc
    return _body_object(value, what)


@dataclass
@final
class HttpSutAdapter(SutAdapter):
    """Drive a SUT over bounded HTTP quote/pay/status endpoints."""

    base_url: str
    timeout: float = 30.0
    _client: Any = None

    def __post_init__(self) -> None:
        """Validate the HTTP timeout before any remote request is possible."""
        if (
            not isinstance(self.timeout, (int, float))
            or isinstance(self.timeout, bool)
            or not math.isfinite(self.timeout)
            or not 0 < self.timeout <= 300
        ):
            raise ValueError("timeout must be finite and within (0, 300]")

    def _http(self) -> Any:
        """Lazily construct and reuse the bounded synchronous HTTP client."""
        if self._client is None:
            import httpx

            self._client = httpx.Client(base_url=self.base_url, timeout=self.timeout)
        return self._client

    def close(self) -> None:
        """Close the persistent HTTP client, if one was opened.

        Clearing the reference makes this operation idempotent and permits a
        deliberately reused adapter to establish a fresh connection later.
        """
        client = self._client
        self._client = None
        if client is not None:
            try:
                client.close()
            except Exception as exc:
                raise SutAdapterError(f"unable to close HTTP client: {exc}") from exc

    def __enter__(self) -> HttpSutAdapter:
        """Return this adapter for managed HTTP-client use."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the persistent HTTP client on context-manager exit."""
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Perform one HTTP request and translate failures to adapter errors."""
        try:
            response = self._http().request(method, path, **kwargs)
            response.raise_for_status()
        except SutAdapterError:
            raise
        except Exception as exc:
            raise SutAdapterError(f"{method} {path}: HTTP failure: {exc}") from exc
        return _decode_response(response, f"{method} {path}")

    def quote(self) -> Quote:
        """POST the quote endpoint and return a strictly parsed quote."""
        return parse_quote(self._request("POST", "/quote"))

    def pay(self, order_id: str, authorization: dict[str, Any]) -> PayResult:
        """POST an authorization and bind the response to the requested order."""
        requested = _order_id(order_id, "requested order_id")
        body = self._request(
            "POST", "/pay", json={"order_id": requested, "authorization": authorization}
        )
        return parse_pay(body, expected_order_id=requested)

    def status(self, order_id: str) -> Status:
        """GET URL-safe order status and bind the response to the request."""
        requested = _order_id(order_id, "requested order_id")
        path = "/status/" + quote_path_segment(requested, safe="")
        return parse_status(self._request("GET", path), expected_order_id=requested)
