"""Offline edge-case tests: HTTP route wiring + small guard branches."""

from __future__ import annotations

import pytest

from psv.quote_option import quote_is_stale
from psv.reconciliation import decode_transfer_log, topic_addr
from psv.reference_sut.confirmer import (
    TOPIC_AUTHORIZATION_USED,
    TOPIC_TRANSFER,
    EventWatchingConfirmer,
    topic_nonce,
)

PAYER = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
MERCHANT = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"


def test_quote_is_stale_with_nonpositive_locked_price() -> None:
    # A free/zero-priced quote is exploitable as soon as fair value is positive.
    assert quote_is_stale(0, 5, tolerance=0.02) is True
    assert quote_is_stale(0, 0, tolerance=0.02) is False


def test_confirmer_skips_non_hex_log_data() -> None:
    token = "0x" + "aa" * 20
    tx_hash = "0x" + "bb" * 32
    nonce = "0x" + "cc" * 32
    invalid = {
        "address": token,
        "topics": [TOPIC_TRANSFER, topic_addr(PAYER), topic_addr(MERCHANT)],
        "data": "not-hex",
        "transactionHash": tx_hash,
        "blockNumber": "0x1",
        "logIndex": "0x1",
    }

    def fetch(addr, topics, from_block):
        return [invalid]

    c = EventWatchingConfirmer(fetch_logs=fetch)
    receipt = {
        "status": "0x1",
        "transactionHash": tx_hash,
        "to": token,
        "blockNumber": "0x1",
        "logs": [
            {
                "address": token,
                "topics": [TOPIC_AUTHORIZATION_USED, topic_addr(PAYER), topic_nonce(nonce)],
                "data": "0x",
                "transactionHash": tx_hash,
                "blockNumber": "0x1",
                "logIndex": "0x0",
            },
            invalid,
        ],
    }
    assert (
        c.is_settled(
            token=token,
            payer=PAYER,
            payee=MERCHANT,
            expected_value=1,
            authorization_nonce=nonce,
            submitted_tx=tx_hash,
            receipt=receipt,
        )
        is False
    )


def test_decode_transfer_log_zero_value() -> None:
    log = {
        "address": TOKEN,
        "topics": [TOPIC_TRANSFER, topic_addr(PAYER), topic_addr(MERCHANT)],
        "data": "0x" + "00" * 32,
        "transactionHash": "0x" + "ee" * 32,
        "blockNumber": "0x1",
        "transactionIndex": "0x0",
        "blockHash": "0x" + "dd" * 32,
        "logIndex": "0x0",
        "removed": False,
    }
    credit = decode_transfer_log(log, chain_id=84532)
    assert credit.value == 0 and credit.tx_hash == "0x" + "ee" * 32


def test_decode_transfer_log_rejects_short_topics() -> None:
    with pytest.raises(ValueError):
        decode_transfer_log({"topics": [TOPIC_TRANSFER], "data": "0x01"}, chain_id=84532)


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
