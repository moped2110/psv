"""Shared fixtures.

Two worlds:
  * **offline** (default): pure-logic units run anywhere, no chain, no network.
  * **onchain** (``-m onchain``): real Anvil + a deployed UpgradeableMockUSDC.
    These run on a dev machine (see README), never in the CI sandbox.

Anvil's well-known dev keys are used deliberately — they are PUBLIC, control only
local test funds, and never touch mainnet. This matches the project's hard line:
no real keys, no mainnet money, ever.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

# Standard, PUBLIC Anvil dev accounts (test-only, documented everywhere).
ANVIL_ACCOUNTS = {
    "deployer": (
        "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    ),
    "payer": (
        "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    ),
    "merchant": (
        "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
    ),
}

DEFAULT_RPC = os.environ.get("PSV_RPC", "http://127.0.0.1:8545")
DEFAULT_CHAIN_ID = int(os.environ.get("PSV_CHAIN_ID", "84532"))
# Anvil's deterministic first-deployment address (deployer nonce 0).
DEFAULT_TOKEN = os.environ.get("PSV_TOKEN", "0x5FbDB2315678afecb367f032d93F642f64180aa3")


def send_tx(rpc: Any, key: str, to: str, data: str, chain_id: int, gas: int = 400_000) -> str:
    """Sign and submit a transaction from ``key``; return the tx hash."""
    from eth_account import Account

    acct = Account.from_key(key)
    nonce = int(rpc.call("eth_getTransactionCount", [acct.address, "pending"]), 16)
    try:
        gas_price = int(rpc.call("eth_gasPrice"), 16)
    except Exception:
        gas_price = 1_000_000_000
    tx = {
        "to": to, "data": data, "value": 0, "gas": gas,
        "gasPrice": gas_price, "nonce": nonce, "chainId": chain_id,
    }
    signed = acct.sign_transaction(tx)
    tx_hash = rpc.send_raw_transaction(signed.raw_transaction.to_0x_hex())
    rpc.wait_for_receipt(tx_hash)
    return tx_hash


@pytest.fixture
def rpc() -> Any:
    """A JSON-RPC client to a running Anvil (onchain tests only)."""
    from psv.anvil import RpcClient

    client = RpcClient(endpoint=DEFAULT_RPC)
    try:
        client.block_number()
    except Exception:
        pytest.skip(f"no Anvil reachable at {DEFAULT_RPC}; start it (see README)")
    return client


@pytest.fixture
def chain_snapshot(rpc: Any) -> Iterator[None]:
    """Snapshot before each onchain test and revert after — perfect isolation."""
    snap = rpc.snapshot()
    try:
        yield
    finally:
        try:
            rpc.revert(snap)
        except Exception:
            pass


@pytest.fixture
def funded_token(rpc: Any, chain_snapshot: None) -> Any:
    """A TokenView with the payer pre-funded; merchant balance reset to a known base."""
    from psv.chain import TokenView

    token = TokenView(rpc, DEFAULT_TOKEN)
    try:
        token.event_mode()
    except Exception:
        pytest.skip(f"no UpgradeableMockUSDC at {DEFAULT_TOKEN}; deploy it (see README)")
    payer = ANVIL_ACCOUNTS["payer"][0]
    deployer_key = ANVIL_ACCOUNTS["deployer"][1]
    # Mint plenty of test USDC to the payer.
    send_tx(rpc, deployer_key, DEFAULT_TOKEN, token.mint_calldata(payer, 1_000_000), DEFAULT_CHAIN_ID)
    return token
