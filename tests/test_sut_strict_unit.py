"""Regression tests for the strict SUT HTTP/wire contract (PSV-AUD-003)."""

from __future__ import annotations

import json

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from psv.sut import (
    HttpSutAdapter,
    Quote,
    SutAdapterError,
    parse_pay,
    parse_quote,
    parse_status,
)

PAYEE = "0x" + "11" * 20
TOKEN = "0x" + "22" * 20
TX_HASH = "0x" + "ab" * 32


def _quote(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "order_id": "ord_strict",
        "amount": "10000",
        "payTo": PAYEE,
        "asset": TOKEN,
        "network": "eip155:84532",
        "extra": {"name": "USDC", "version": "2"},
    }
    body.update(overrides)
    return body


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, [], {}])
def test_pay_rejects_non_boolean_settled(value: object) -> None:
    with pytest.raises(SutAdapterError, match="settled: expected boolean"):
        parse_pay({"order_id": "ord", "submitted_tx": None, "settled": value})


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, [], {}])
def test_status_rejects_non_boolean_paid(value: object) -> None:
    with pytest.raises(SutAdapterError, match="paid: expected boolean"):
        parse_status({"order_id": "ord", "submitted_tx": None, "resource": None, "paid": value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("amount", 1),
        ("amount", "0"),
        ("amount", "-1"),
        ("amount", "01"),
        ("amount", str(2**256)),
        ("payTo", "0x1"),
        ("asset", "0x" + "11" * 19),
        ("network", "solana:mainnet"),
        ("network", "eip155:0"),
        ("network", "eip155:084532"),
        ("network", f"eip155:{2**256}"),
    ],
)
def test_quote_rejects_invalid_wire_domains(field: str, value: object) -> None:
    with pytest.raises(SutAdapterError):
        parse_quote(_quote(**{field: value}))


@pytest.mark.parametrize(
    "body",
    [
        {key: value for key, value in _quote().items() if key != "extra"},
        _quote(extra={"name": "USDC"}),
        _quote(extra={"version": "2"}),
        _quote(extra=None),
    ],
)
def test_quote_requires_signing_domain_fields(body: dict[str, object]) -> None:
    with pytest.raises(SutAdapterError):
        parse_quote(body)


def test_pay_and_status_require_matching_order_id_and_hash_shape() -> None:
    with pytest.raises(SutAdapterError, match="does not match request"):
        parse_pay(
            {"order_id": "other", "submitted_tx": None, "settled": False},
            expected_order_id="requested",
        )
    with pytest.raises(SutAdapterError, match="transaction hash"):
        parse_status(
            {"order_id": "ord", "paid": True, "resource": "x", "submitted_tx": "0x1"},
            expected_order_id="ord",
        )


@given(
    value=st.one_of(
        st.integers(), st.none(), st.lists(st.integers()), st.dictionaries(st.text(), st.integers())
    )
)
def test_property_non_boolean_paid_is_never_truthiness_coerced(value: object) -> None:
    with pytest.raises(SutAdapterError):
        parse_status({"order_id": "ord", "paid": value, "resource": None, "submitted_tx": None})


def _adapter(handler: httpx.MockTransport) -> HttpSutAdapter:
    client = httpx.Client(base_url="http://sut.test", transport=handler)
    return HttpSutAdapter(base_url="http://sut.test", _client=client)


@pytest.mark.parametrize(
    "content",
    [
        b"not json",
        b"[]",
        b'{"order_id":"one","order_id":"two"}',
        b'{"order_id":"ord","amount":"1","payTo":"0x' + b"11" * 20,
    ],
)
def test_adapter_normalizes_malformed_or_duplicate_json(content: bytes) -> None:
    adapter = _adapter(httpx.MockTransport(lambda _request: httpx.Response(200, content=content)))
    with pytest.raises(SutAdapterError):
        adapter.quote()


def test_adapter_bounds_response_size() -> None:
    content = json.dumps(_quote(padding="x" * (1024 * 1024))).encode()
    adapter = _adapter(httpx.MockTransport(lambda _request: httpx.Response(200, content=content)))
    with pytest.raises(SutAdapterError, match="exceeds"):
        adapter.quote()


def test_status_percent_encodes_hostile_order_id_as_one_path_segment() -> None:
    order_id = "ord/../secret?x=1#fragment space"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path == b"/status/ord%2F..%2Fsecret%3Fx%3D1%23fragment%20space"
        return httpx.Response(
            200,
            json={"order_id": order_id, "paid": False, "resource": None, "submitted_tx": None},
        )

    assert _adapter(httpx.MockTransport(handler)).status(order_id).paid is False


def test_http_and_timeout_failures_use_adapter_error() -> None:
    adapter = _adapter(httpx.MockTransport(lambda _request: httpx.Response(503)))
    with pytest.raises(SutAdapterError, match="HTTP failure"):
        adapter.quote()
    for timeout in (0, -1, float("nan"), float("inf"), 301):
        with pytest.raises(ValueError):
            HttpSutAdapter("http://sut.test", timeout=timeout)


def test_direct_quote_construction_still_validates_chain_id_on_access() -> None:
    quote = Quote("ord", 1, PAYEE, TOKEN, "not-caip-2", "USDC", "2")
    with pytest.raises(SutAdapterError, match="invalid CAIP-2"):
        _ = quote.chain_id


@pytest.mark.parametrize("value", [None, 7, "", "x" * 16_385])
def test_signing_domain_strings_are_required_and_bounded(value: object) -> None:
    with pytest.raises(SutAdapterError):
        parse_quote(_quote(extra={"name": value, "version": "2"}))


@pytest.mark.parametrize("order_id", ["line\nbreak", "x" * 257])
def test_order_ids_reject_control_characters_and_excessive_length(order_id: str) -> None:
    with pytest.raises(SutAdapterError, match="invalid order id"):
        parse_quote(_quote(order_id=order_id))


def test_adapter_context_manager_closes_injected_client() -> None:
    client = httpx.Client(
        base_url="http://sut.test",
        transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
    )
    with HttpSutAdapter("http://sut.test", _client=client) as adapter:
        assert adapter is not None
    assert client.is_closed


def test_adapter_normalizes_response_read_and_close_failures() -> None:
    class BrokenResponse:
        @property
        def content(self) -> bytes:
            raise OSError("broken body")

        def raise_for_status(self) -> None:
            return None

    class BrokenClient:
        def request(self, method: str, path: str, **kwargs: object) -> BrokenResponse:
            return BrokenResponse()

        def close(self) -> None:
            raise OSError("broken close")

    adapter = HttpSutAdapter("http://sut.test", _client=BrokenClient())
    with pytest.raises(SutAdapterError, match="unable to read HTTP response"):
        adapter.quote()
    with pytest.raises(SutAdapterError, match="unable to close HTTP client"):
        adapter.close()
