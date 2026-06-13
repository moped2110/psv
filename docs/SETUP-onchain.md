# Running the on-chain tests (dev machine)

The offline logic tests run anywhere. The end-to-end and SC1 tests need a local
Anvil chain plus a deployed `UpgradeableMockUSDC`. Everything below uses Anvil's
**public** dev keys and local test funds only — never mainnet.

## Prerequisites

- [Foundry](https://book.getfoundry.sh/getting-started/installation) (`anvil`, `forge`)
- Python env with the harness installed: `pip install -e ".[dev]"`

## 1. Start Anvil (chain-id must match the signing domain)

```bash
anvil --chain-id 84532
```

Leave it running. It prints ten funded accounts; the harness uses the first three
(deployer, payer, merchant) — these match the keys in `tests/conftest.py`.

## 2. Deploy the token

The first contract deployed by Anvil account #0 lands at the deterministic
address the harness expects
(`0x5FbDB2315678afecb367f032d93F642f64180aa3`). From `onchain/`:

```bash
cd onchain
forge create src/UpgradeableMockUSDC.sol:UpgradeableMockUSDC \
  --rpc-url http://127.0.0.1:8545 \
  --private-key 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80 \
  --broadcast
```

Confirm the printed `Deployed to:` equals the address above. If you deployed
something else to account #0 first, restart Anvil so the nonce is 0 — or set
`PSV_TOKEN` to the actual address before running the tests.

## 3. Run the on-chain tests

```bash
pytest -m onchain
```

Each test snapshots Anvil before it runs and reverts after, so they are
order-independent and repeatable without restarting the chain.

## Environment overrides

| Variable | Default | Meaning |
|---|---|---|
| `PSV_RPC` | `http://127.0.0.1:8545` | Anvil endpoint |
| `PSV_CHAIN_ID` | `84532` | chain id (must match the `anvil --chain-id`) |
| `PSV_TOKEN` | `0x5FbD…0aa3` | deployed `UpgradeableMockUSDC` address |

## Running the reference SUT as a standalone service (optional)

The on-chain tests drive the reference SUT in-process. To run it as a real HTTP
service (e.g. to exercise the `HttpSutAdapter` over the wire):

```bash
PSV_MERCHANT=0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC \
PSV_FACILITATOR_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80 \
python -m psv.reference_sut.server      # serves on 127.0.0.1:8402
```
