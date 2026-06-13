# psv — payment-system verification harness

A system-level test harness for **complete x402 payment systems**. Where a
black-box conformance tester checks whether an endpoint *speaks the protocol*,
`psv` verifies whether the system *behind* that endpoint **pays, books and
recovers correctly** under real chain conditions: settlement detection, RPC
resilience, idempotency, reorg handling, reconciliation.

## The core idea: an independent chain-truth oracle

A payment system holds a *belief* about each payment ("order is paid / unpaid").
`psv` holds a second, independent record read **directly from the chain** —
balances moved, EIP-3009 nonce burned, settlement event emitted. When the two
disagree, that gap is the bug:

| chain truth | system belief | verdict |
|---|---|---|
| funds moved | paid | ✅ consistent |
| no funds | unpaid | ✅ consistent |
| **funds moved** | **unpaid** | 🔴 **silent loss** — customer paid, gets nothing |
| **no funds** | **paid** | 🔴 **phantom credit** — resource handed out free |

The two red cases are invisible to black-box conformance. Catching them is the
entire point of a system-level harness.

## What's in the box

- A **controllable chain** wrapper over [Anvil](https://book.getfoundry.sh/anvil/)
  (`psv.anvil`): snapshot/revert per test, mine, advance time, `eth_getLogs`.
- A **chain-truth oracle** (`psv.chain`): reads balances, nonce state and the
  drift-proof `AuthorizationUsed` event straight from the token.
- A **SUT adapter contract** (`psv.sut`): a tiny HTTP interface (`quote` / `pay`
  / `status`) so the same tests run against any payment system.
- A **bundled reference SUT** (`psv.reference_sut`): a faithful miniature payment
  system — quote, on-chain settle, **event-watching confirmation**, resource
  unlock — used to exercise the harness end to end.
- A **divergence detector** (`psv.divergence`): grades chain-truth vs. belief.

## Demonstrated findings (top-damage scenarios)

Each is reproduced end-to-end against the bundled reference SUT and caught by the
harness — none are visible to black-box conformance:

- **SC1 — contract / event drift.** A token changes its settlement event
  signature *in place* (a proxy upgrade); payments keep moving funds, but the
  system's event filter goes blind and reports every order unpaid → **silent
  loss**. [`docs/sc1-abi-drift.md`](docs/sc1-abi-drift.md)
- **D3 — backup/restore vs. chain divergence.** A ledger rollback forgets a
  payment that settled on-chain; reconciliation (chain credits minus ledger
  records) surfaces and heals exactly the gap. [`docs/d3-reconciliation.md`](docs/d3-reconciliation.md)
- **G3 — quote as a free option.** A locked quote honored after the fair value
  moves is a free call option; a re-pricing guard rejects stale quotes before
  settlement. [`docs/g3-quote-option.md`](docs/g3-quote-option.md)

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"     # chain + sut + test tooling
```

## Run

Offline logic tests run anywhere (no chain):

```bash
pytest                      # on-chain + load tests are deselected by default
```

The on-chain end-to-end and SC1 tests need Anvil + a deployed token — see
[`docs/SETUP-onchain.md`](docs/SETUP-onchain.md):

```bash
pytest -m onchain
```

## Safety

No mainnet money, ever. The harness runs against a local Anvil chain using
Anvil's well-known **public** development keys, which control only local test
funds. No custody, no payment service, no advice — this is a test tool.

## License

Apache-2.0.
