"""Real-wire calibration of :class:`HttpSutAdapter` against the reference SUT.

Every server binds only to loopback.  The settlement test uses the public Anvil
development accounts and the local MockUSDC deployment; it must never be aimed
at a live or mainnet RPC endpoint.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import uvicorn
from conftest import ANVIL_ACCOUNTS, DEFAULT_CHAIN_ID, DEFAULT_RPC, DEFAULT_TOKEN, send_tx
from fastapi import FastAPI, Response

from psv.chain import TokenView
from psv.divergence import DivergenceKind
from psv.payloads import EvmSigner, sign_authorization
from psv.rails import LiveReconciliation, get_rail, reconcile_live
from psv.reconciliation import TOPIC_TRANSFER, decode_transfer_log
from psv.reference_sut.server import ReferenceSut, SutConfig, create_app
from psv.sut import (
    HttpSutAdapter,
    PayResult,
    Quote,
    Status,
    SutAdapter,
    SutAdapterError,
    parse_pay,
    parse_quote,
    parse_status,
)

pytestmark = pytest.mark.onchain


@contextmanager
def _running_uvicorn(app: Any) -> Iterator[str]:
    """Serve one ASGI app on a pre-bound loopback socket and stop it fully."""

    async def test_readiness() -> dict[str, bool]:
        return {"ready": True}

    app.add_api_route(
        "/__psv_test_ready",
        test_readiness,
        methods=["GET"],
        include_in_schema=False,
    )
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            access_log=False,
            log_config=None,
            lifespan="on",
        )
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        name=f"psv-test-uvicorn-{port}",
        daemon=False,
    )
    base_url = f"http://127.0.0.1:{port}"
    thread.start()
    try:
        deadline = time.monotonic() + 5.0
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if not thread.is_alive():
                raise RuntimeError("uvicorn stopped before its readiness probe succeeded")
            try:
                response = httpx.get(base_url + "/__psv_test_ready", timeout=0.2, trust_env=False)
                if response.status_code == 200:
                    break
            except httpx.TransportError as exc:
                last_error = exc
            time.sleep(0.02)
        else:
            raise RuntimeError(f"uvicorn readiness timed out: {last_error}")
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=5.0)
        listener.close()
        if thread.is_alive():
            raise RuntimeError("uvicorn did not stop during deterministic teardown")


def _config() -> SutConfig:
    return SutConfig(
        token_address=DEFAULT_TOKEN,
        merchant_address=ANVIL_ACCOUNTS["merchant"][0],
        facilitator_key=ANVIL_ACCOUNTS["deployer"][1],
        chain_id=DEFAULT_CHAIN_ID,
        rpc_endpoint=DEFAULT_RPC,
    )


@dataclass
class _InProcessAdapter(SutAdapter):
    """Apply the same strict wire normalization without an HTTP hop."""

    sut: ReferenceSut

    def quote(self) -> Quote:
        return parse_quote(self.sut.quote())

    def pay(self, order_id: str, authorization: dict[str, Any]) -> PayResult:
        return parse_pay(self.sut.pay(order_id, authorization), expected_order_id=order_id)

    def status(self, order_id: str) -> Status:
        return parse_status(self.sut.status(order_id), expected_order_id=order_id)


def _transfer_log_index(receipt: dict[str, Any]) -> int:
    logs = receipt["logs"]
    assert isinstance(logs, list)
    transfers = [
        decode_transfer_log(log, chain_id=DEFAULT_CHAIN_ID)
        for log in logs
        if isinstance(log, dict)
        and isinstance(log.get("topics"), list)
        and log["topics"]
        and str(log["topics"][0]).lower() == TOPIC_TRANSFER
    ]
    assert len(transfers) == 1
    return transfers[0].log_index


def _settle_and_reconcile(adapter: SutAdapter, token: TokenView) -> LiveReconciliation:
    payer = EvmSigner.from_key(ANVIL_ACCOUNTS["payer"][1])
    merchant = ANVIL_ACCOUNTS["merchant"][0]
    quote = adapter.quote()
    assert quote.chain_id == DEFAULT_CHAIN_ID
    assert quote.asset.lower() == DEFAULT_TOKEN.lower()
    assert quote.pay_to.lower() == merchant.lower()

    payer_before = token.balance_of(payer.address)
    payee_before = token.balance_of(merchant)
    authorization = sign_authorization(
        signer=payer,
        to=merchant,
        value=quote.amount,
        chain_id=quote.chain_id,
        token_address=quote.asset,
        token_name=quote.token_name,
        token_version=quote.token_version,
    )
    payment = adapter.pay(quote.order_id, authorization.as_dict())
    status = adapter.status(quote.order_id)
    assert payment.settled is True
    assert payment.submitted_tx is not None
    assert status.paid is True
    assert status.submitted_tx == payment.submitted_tx
    assert status.resource == f"premium-content::{quote.order_id}"

    receipt = token.rpc.get_transaction_receipt(payment.submitted_tx)
    result = reconcile_live(
        token,
        get_rail("mock-anvil"),
        payer=payer.address,
        payee=merchant,
        nonce=authorization.nonce,
        transaction_hash=payment.submitted_tx,
        log_index=_transfer_log_index(receipt),
        required_amount=quote.amount,
        payer_before=payer_before,
        payee_before=payee_before,
        sut_believes_paid=status.paid,
    )
    assert result.kind is DivergenceKind.CONSISTENT_PAID
    assert result.evidence.receipt_status == 1
    assert result.evidence.nonce_consumed is True
    assert result.evidence.removed is False
    assert result.evidence.event_value == quote.amount
    assert result.evidence.received_amount == quote.amount
    return result


def _semantic_evidence(result: LiveReconciliation) -> tuple[object, ...]:
    evidence = result.evidence
    return (
        result.kind,
        evidence.chain_id,
        evidence.receipt_status,
        evidence.event_value,
        evidence.required_amount,
        evidence.received_amount,
        evidence.payer_balance_after - evidence.payer_balance_before,
        evidence.payee_balance_after - evidence.payee_balance_before,
        evidence.nonce_consumed,
        evidence.removed,
        evidence.token_address,
        evidence.payer,
        evidence.payee,
    )


def test_real_http_path_matches_in_process_chain_truth(rpc: Any, funded_token: TokenView) -> None:
    send_tx(
        rpc,
        ANVIL_ACCOUNTS["deployer"][1],
        DEFAULT_TOKEN,
        funded_token.set_event_mode_calldata(0),
        DEFAULT_CHAIN_ID,
    )

    app = create_app(_config())
    with _running_uvicorn(app) as base_url:
        readiness = httpx.get(base_url + "/__psv_test_ready", timeout=1, trust_env=False)
        assert readiness.status_code == 200 and readiness.json() == {"ready": True}
        with HttpSutAdapter(base_url=base_url, timeout=5) as adapter:
            http_result = _settle_and_reconcile(adapter, funded_token)

    with pytest.raises(httpx.TransportError):
        httpx.get(base_url + "/__psv_test_ready", timeout=0.2, trust_env=False)

    in_process_result = _settle_and_reconcile(
        _InProcessAdapter(ReferenceSut(_config())), funded_token
    )
    assert _semantic_evidence(http_result) == _semantic_evidence(in_process_result)


def test_real_http_malformed_body_and_timeout_are_adapter_errors() -> None:
    malformed_app = FastAPI()

    @malformed_app.post("/quote")
    def malformed_quote() -> Response:
        return Response(content=b'{"broken":', media_type="application/json")

    with _running_uvicorn(malformed_app) as base_url:
        with HttpSutAdapter(base_url=base_url, timeout=1) as adapter:
            with pytest.raises(SutAdapterError, match="malformed JSON"):
                adapter.quote()

    slow_app = FastAPI()

    @slow_app.post("/quote")
    async def slow_quote() -> dict[str, object]:
        await asyncio.sleep(0.25)
        return _valid_quote()

    with _running_uvicorn(slow_app) as base_url:
        with HttpSutAdapter(base_url=base_url, timeout=0.02) as adapter:
            with pytest.raises(SutAdapterError, match="HTTP failure"):
                adapter.quote()


def _valid_quote() -> dict[str, object]:
    return {
        "order_id": "ord_lifecycle",
        "amount": "1",
        "payTo": ANVIL_ACCOUNTS["merchant"][0],
        "asset": DEFAULT_TOKEN,
        "network": f"eip155:{DEFAULT_CHAIN_ID}",
        "extra": {"name": "USDC", "version": "2"},
    }


def test_http_adapter_context_closes_and_can_reopen_connection() -> None:
    app = FastAPI()

    @app.post("/quote")
    def quote() -> dict[str, object]:
        return _valid_quote()

    @app.get("/status/{order_id}")
    def status(order_id: str) -> dict[str, object]:
        return {
            "order_id": order_id,
            "paid": False,
            "resource": None,
            "submitted_tx": None,
        }

    with _running_uvicorn(app) as base_url:
        adapter = HttpSutAdapter(base_url=base_url, timeout=1)
        with adapter as active:
            assert active.quote().order_id == "ord_lifecycle"
            first_client = adapter._client
            assert first_client is not None
            assert active.status("ord_lifecycle").paid is False
            assert adapter._client is first_client
        assert first_client.is_closed
        assert adapter._client is None

        assert adapter.quote().order_id == "ord_lifecycle"
        second_client = adapter._client
        assert second_client is not None and second_client is not first_client
        adapter.close()
        adapter.close()
        assert second_client.is_closed
