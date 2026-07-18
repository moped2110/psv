"""Load — sequential settlement throughput (Phase 5). On-chain, against Anvil.

Drives N real settlements through the SUT and reports throughput/latency, while
asserting correctness under load: every payment settles, and the merchant is
credited exactly N times (no dropped or double settlements).

Sequential (concurrency=1): true concurrency from a single facilitator account
hits the sender-nonce race — that needs a pool of facilitator accounts and is a
later step. This establishes the profile runner against real settlements.

Run: pytest -m load tests/test_load_throughput.py
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import ANVIL_ACCOUNTS, DEFAULT_CHAIN_ID, DEFAULT_RPC, DEFAULT_TOKEN, send_tx

from psv.chain import TokenView
from psv.load import FacilitatorTaskPool, run_profile, run_staged_profile, standard_profile
from psv.payloads import EvmSigner, sign_authorization
from psv.reference_sut.server import ReferenceSut, SutConfig

pytestmark = pytest.mark.load

N = 5


def test_sequential_settlement_throughput(rpc: Any, funded_token: TokenView) -> None:
    token = funded_token
    send_tx(
        rpc,
        ANVIL_ACCOUNTS["deployer"][1],
        DEFAULT_TOKEN,
        token.set_event_mode_calldata(0),
        DEFAULT_CHAIN_ID,
    )
    sut = ReferenceSut(
        SutConfig(
            token_address=DEFAULT_TOKEN,
            merchant_address=ANVIL_ACCOUNTS["merchant"][0],
            facilitator_key=ANVIL_ACCOUNTS["deployer"][1],
            chain_id=DEFAULT_CHAIN_ID,
            rpc_endpoint=DEFAULT_RPC,
        )
    )
    payer = EvmSigner.from_key(ANVIL_ACCOUNTS["payer"][1])
    merchant = ANVIL_ACCOUNTS["merchant"][0]
    amount = sut.config.price
    merchant_before = token.balance_of(merchant)

    def task(i: int) -> None:
        quote = sut.quote()
        auth = sign_authorization(
            signer=payer,
            to=merchant,
            value=amount,
            chain_id=DEFAULT_CHAIN_ID,
            token_address=DEFAULT_TOKEN,
            token_name="USDC",
            token_version="2",
        )
        result = sut.pay(quote["order_id"], auth.as_dict())
        assert result["settled"] is True

    res = run_profile(task, iterations=N, concurrency=1)

    assert res.errors == 0
    assert res.ok == N
    assert token.balance_of(merchant) - merchant_before == N * amount  # each settled once
    assert res.throughput_per_s > 0
    assert res.p50_ms > 0 and res.max_ms >= res.p95_ms >= res.p50_ms


def test_concurrent_facilitator_pool_ramp_spike_soak_breakpoint_recovery(
    rpc: Any, funded_token: TokenView
) -> None:
    from eth_account import Account

    Account.enable_unaudited_hdwallet_features()
    mnemonic = "test test test test test test test test test test test junk"
    facilitator_indices = (0, 3, 4, 5, 6, 7, 8, 9)
    suts = [
        ReferenceSut(
            SutConfig(
                token_address=DEFAULT_TOKEN,
                merchant_address=ANVIL_ACCOUNTS["merchant"][0],
                facilitator_key=Account.from_mnemonic(
                    mnemonic, account_path=f"m/44'/60'/0'/0/{index}"
                ).key.to_0x_hex(),
                chain_id=DEFAULT_CHAIN_ID,
                rpc_endpoint=DEFAULT_RPC,
            )
        )
        for index in facilitator_indices
    ]
    payer = EvmSigner.from_key(ANVIL_ACCOUNTS["payer"][1])
    merchant = ANVIL_ACCOUNTS["merchant"][0]
    amount = suts[0].config.price
    merchant_before = funded_token.balance_of(merchant)

    def worker(sut: ReferenceSut):
        def settle(_: int) -> None:
            quote = sut.quote()
            authorization = sign_authorization(
                signer=payer,
                to=merchant,
                value=amount,
                chain_id=DEFAULT_CHAIN_ID,
                token_address=DEFAULT_TOKEN,
                token_name="USDC",
                token_version="2",
            )
            assert sut.pay(quote["order_id"], authorization.as_dict())["settled"] is True

        return settle

    pool = FacilitatorTaskPool(tuple(worker(sut) for sut in suts))
    result = run_staged_profile(pool, standard_profile(scale=8, peak_concurrency=4))

    assert result.errors == 0
    assert result.ok == result.total == 72
    assert result.recovered is True
    assert funded_token.balance_of(merchant) - merchant_before == result.ok * amount
    assert result.as_dict()["correctness"]["successful"] == 72
