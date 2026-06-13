"""Offline edge-case tests: HTTP route wiring + small guard branches."""

from __future__ import annotations

import pytest

from psv.quote_option import quote_is_stale
from psv.reconciliation import decode_transfer_log, topic_addr
from psv.reference_sut.confirmer import TOPIC_TRANSFER, EventWatchingConfirmer

PAYER = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
MERCHANT = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"


def test_quote_is_stale_with_nonpositive_locked_price() -> None:
    # A free/zero-priced quote is exploitable as soon as fair value is positive.
    assert quote_is_stale(0, 5, tolerance=0.02) is True
    assert quote_is_stale(0, 0, tolerance=0.02) is False


def test_confirmer_skips_non_hex_log_data() -> None:
    def fetch(addr, topics, from_block):
        return [{"topics": topics, "data": "not-hex"}]

    c = EventWatchingConfirmer(fetch_logs=fetch)
    assert c.is_settled(token="0xt", payer=PAYER, payee=MERCHANT, min_value=1) is False


def test_decode_transfer_log_zero_value() -> None:
    log = {
        "topics": [TOPIC_TRANSFER, topic_addr(PAYER), topic_addr(MERCHANT)],
        "data": "0x",
        "transactionHash": "0xEE",
    }
    credit = decode_transfer_log(log)
    assert credit.value == 0 and credit.tx_hash == "0xee"


def test_decode_transfer_log_rejects_short_topics() -> None:
    with pytest.raises(ValueError):
        decode_transfer_log({"topics": [TOPIC_TRANSFER], "data": "0x01"})


# --- reference SUT HTTP surface (no chain needed for these paths) ------------

DEPLOYER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TOKEN = "0x5FbDB2315678afecb367f032d93F642f64180aa3"


def test_app_routes_for_unknown_order() -> None:
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient

    from psv.reference_sut.server import SutConfig, create_app

    app = create_app(
        SutConfig(token_address=TOKEN, merchant_address=MERCHANT, facilitator_key=DEPLOYER_KEY)
    )
    client = TestClient(app)

    status = client.get("/status/ord_nope")
    assert status.status_code == 200 and status.json()["known"] is False

    resource = client.get("/resource/ord_nope")
    assert resource.status_code == 402  # unpaid -> payment required
