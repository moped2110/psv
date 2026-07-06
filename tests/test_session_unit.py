"""Offline tests for N15 — session / order-id predictability."""

from __future__ import annotations

import pytest

pytest.importorskip("eth_account")

from psv.reference_sut.server import ReferenceSut, SutConfig
from psv.security_checks import sufficient_id_entropy

DEPLOYER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TOKEN = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
MERCHANT = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"


class _Rpc:
    def block_number(self) -> int:
        return 100


def test_sequential_ids_are_guessable() -> None:
    assert sufficient_id_entropy("ord_1") is False
    assert sufficient_id_entropy("ord_42") is False
    assert sufficient_id_entropy("ord_0001") is False  # short + an attacker enumerates


def test_random_ids_pass() -> None:
    assert sufficient_id_entropy("ord_" + "ab" * 8) is True  # 16 hex chars
    assert sufficient_id_entropy("ord_deadbeefdeadbeef") is True


def test_non_hex_body_fails() -> None:
    assert sufficient_id_entropy("ord_not-random!!") is False


def test_reference_sut_issues_unpredictable_ids() -> None:
    sut = ReferenceSut(
        SutConfig(token_address=TOKEN, merchant_address=MERCHANT, facilitator_key=DEPLOYER_KEY)
    )
    sut.rpc = _Rpc()  # type: ignore[assignment]
    ids = {sut.quote()["order_id"] for _ in range(50)}
    assert len(ids) == 50  # no collisions across 50 quotes
    assert all(sufficient_id_entropy(oid) for oid in ids)  # every id is high-entropy
