"""The reference SUT must refuse unsafe signing and submission fail-closed."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("eth_account")

from psv.reference_sut.server import ReferenceSut, SutConfig
from psv.safety import SettlementSafetyError, SettlementSafetyPolicy

DEPLOYER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TOKEN = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
MERCHANT = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
PAYER = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


def _authorization(
    *, to: str = MERCHANT, payer: str = PAYER, value: object = "10000"
) -> dict[str, Any]:
    return {
        "from": payer,
        "to": to,
        "value": value,
        "validAfter": "0",
        "validBefore": str(2**48),
        "nonce": "0x" + "ab" * 32,
        "signature": "0x" + "11" * 65,
    }


class _Rpc:
    def __init__(self, *, chain_id: int, code: str = "0x6000") -> None:
        self.chain_id = chain_id
        self.code = code
        self.calls: list[str] = []
        self.sent: list[str] = []

    def call(self, method: str, params: list[object] | None = None) -> object:
        self.calls.append(method)
        if method == "eth_chainId":
            return hex(self.chain_id)
        if method == "eth_getTransactionCount":
            return "0x0"
        if method == "eth_gasPrice":
            return "0x1"
        raise AssertionError(f"unexpected RPC method {method}")

    def get_code(self, address: str, block: str = "latest") -> str:
        self.calls.append("eth_getCode")
        return self.code

    def send_raw_transaction(self, raw: str) -> str:
        self.sent.append(raw)
        return "0x" + "ee" * 32


class _RawSafetyRpc:
    def __init__(self, chain_id: object, code: object = "0x6000") -> None:
        self.chain_id = chain_id
        self.code = code

    def call(self, method: str, params: list[object] | None = None) -> object:
        assert method == "eth_chainId"
        return self.chain_id

    def get_code(self, address: str, block: str = "latest") -> Any:
        return self.code


def _require_safe(rpc: _RawSafetyRpc, **overrides: object) -> None:
    arguments: dict[str, object] = {
        "rpc": rpc,
        "configured_chain_id": 84532,
        "token_address": TOKEN,
        "payer_address": PAYER,
        "payee_address": MERCHANT,
        "authorization_to": MERCHANT,
        "authorization_amount": "10000",
        "expected_amount": 10000,
    }
    arguments.update(overrides)
    SettlementSafetyPolicy().require_safe_submission(**arguments)  # type: ignore[arg-type]


class _Token:
    def __init__(self) -> None:
        self.constructed = 0

    def settle_calldata(self, **kwargs: Any) -> str:
        self.constructed += 1
        return "0x1234"


class _Account:
    address = "0x" + "44" * 20

    def __init__(self) -> None:
        self.signed = 0

    def sign_transaction(self, tx: dict[str, Any]) -> Any:
        self.signed += 1
        raise AssertionError("unsafe request reached signing")


def _sut(*, configured_chain: int, rpc_chain: int, code: str = "0x6000") -> tuple[Any, ...]:
    sut = ReferenceSut(
        SutConfig(
            token_address=TOKEN,
            merchant_address=MERCHANT,
            facilitator_key=DEPLOYER_KEY,
            chain_id=configured_chain,
        )
    )
    rpc = _Rpc(chain_id=rpc_chain, code=code)
    token = _Token()
    account = _Account()
    sut.rpc = rpc  # type: ignore[assignment]
    sut.token = token  # type: ignore[assignment]
    sut.account = account  # type: ignore[assignment]
    return sut, rpc, token, account


@pytest.mark.parametrize("chain_id", [1, 10, 137, 8453, 42161, 999999])
def test_mainnet_and_unknown_chain_refused_before_rpc_or_signing(chain_id: int) -> None:
    sut, rpc, token, account = _sut(configured_chain=chain_id, rpc_chain=chain_id)
    with pytest.raises(SettlementSafetyError, match="allowlist"):
        sut._submit_settlement(_authorization())
    assert rpc.calls == []
    assert token.constructed == account.signed == 0
    assert rpc.sent == []


def test_rpc_chain_mismatch_refused_before_calldata_signing_or_sending() -> None:
    sut, rpc, token, account = _sut(configured_chain=84532, rpc_chain=1)
    with pytest.raises(SettlementSafetyError, match="RPC chain mismatch"):
        sut._submit_settlement(_authorization())
    assert rpc.calls == ["eth_chainId"]
    assert token.constructed == account.signed == 0
    assert rpc.sent == []


@pytest.mark.parametrize("code", ["0x", "0x0", "0x00"])
def test_eoa_token_refused_before_calldata_signing_or_sending(code: str) -> None:
    sut, rpc, token, account = _sut(configured_chain=84532, rpc_chain=84532, code=code)
    with pytest.raises(SettlementSafetyError, match="no deployed contract code"):
        sut._submit_settlement(_authorization())
    assert rpc.calls == ["eth_chainId", "eth_getCode"]
    assert token.constructed == account.signed == 0
    assert rpc.sent == []


@pytest.mark.parametrize("payee", ["0x0", "0x" + "00" * 20, PAYER])
def test_invalid_or_redirected_payee_refused_before_signing(payee: str) -> None:
    sut, rpc, token, account = _sut(configured_chain=84532, rpc_chain=84532)
    with pytest.raises(SettlementSafetyError):
        sut._submit_settlement(_authorization(to=payee))
    assert token.constructed == account.signed == 0
    assert rpc.sent == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("token_address", "0x1234"),
        ("token_address", "0x" + "00" * 20),
        ("merchant_address", "not-an-address"),
        ("merchant_address", "0x" + "00" * 20),
    ],
)
def test_invalid_configured_addresses_are_refused_before_signing(field: str, value: str) -> None:
    sut, rpc, token, account = _sut(configured_chain=84532, rpc_chain=84532)
    setattr(sut.config, field, value)
    with pytest.raises(SettlementSafetyError):
        sut._submit_settlement(_authorization())
    assert token.constructed == account.signed == 0
    assert rpc.sent == []


@pytest.mark.parametrize("payer", ["0x1234", "0x" + "00" * 20, "not-an-address"])
def test_invalid_payer_is_refused_before_calldata_or_signing(payer: str) -> None:
    sut, rpc, token, account = _sut(configured_chain=84532, rpc_chain=84532)
    with pytest.raises(SettlementSafetyError, match="payer"):
        sut._submit_settlement(_authorization(payer=payer))
    assert token.constructed == account.signed == 0
    assert rpc.sent == []


@pytest.mark.parametrize("value", ["9999", "10001", "-1", "1.0", True, None])
def test_non_exact_amount_is_refused_before_calldata_or_signing(value: object) -> None:
    sut, rpc, token, account = _sut(configured_chain=84532, rpc_chain=84532)
    with pytest.raises(SettlementSafetyError, match="amount"):
        sut._submit_settlement(_authorization(value=value))
    assert token.constructed == account.signed == 0
    assert rpc.sent == []


@pytest.mark.parametrize("chain_id", [None, 84532, "84532", "0x", "0xnope", "0x0"])
def test_malformed_or_invalid_rpc_chain_id_fails_closed(chain_id: object) -> None:
    with pytest.raises(SettlementSafetyError, match="chainId"):
        _require_safe(_RawSafetyRpc(chain_id))


@pytest.mark.parametrize("code", [None, 1, "6000", "0xgg", "0x1"])
def test_malformed_contract_code_fails_closed(code: object) -> None:
    with pytest.raises(SettlementSafetyError, match="getCode"):
        _require_safe(_RawSafetyRpc("0x14a34", code))


def test_non_hexadecimal_address_is_distinct_from_wrong_length() -> None:
    with pytest.raises(SettlementSafetyError, match="hexadecimal"):
        _require_safe(_RawSafetyRpc("0x14a34"), payer_address="0x" + "zz" * 20)


@pytest.mark.parametrize("expected", [0, -1, 2**256])
def test_configured_amount_outside_payment_bounds_fails_closed(expected: int) -> None:
    with pytest.raises(SettlementSafetyError, match="outside uint256"):
        _require_safe(
            _RawSafetyRpc("0x14a34"),
            expected_amount=expected,
            authorization_amount=expected,
        )
