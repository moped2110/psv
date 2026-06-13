# Architecture

`psv` verifies a **complete payment system** (the System-under-Test, "SUT"), not
a single endpoint. It needs two things that black-box testing does not: a SUT it
can drive over a stable interface, and a chain it can *manipulate and read as
ground truth*.

```
        signs EIP-3009                 settles on-chain
 payer ───────────────►  SUT  ─────────────────────────►  Anvil (token)
                          │  ▲                                  ▲
                  quote / │  │ status                          │ balances, nonce,
                   pay    │  │ (belief)                        │ AuthorizationUsed
                          ▼  │                                  │ (ground truth)
                    ┌─────────────┐   chain truth   ┌───────────────────────┐
                    │  SUT adapter│◄───────────────►│  chain-truth oracle    │
                    └──────┬──────┘                 └───────────┬───────────┘
                           │     belief  vs  truth              │
                           ▼                                    ▼
                        ┌──────────────────────────────────────────┐
                        │            divergence detector            │
                        └──────────────────────────────────────────┘
```

## Modules

| Module | Role |
|---|---|
| `psv.anvil` | `RpcClient` (JSON-RPC 2.0, injectable transport) + `AnvilProcess` (subprocess manager). Chain manipulation: `snapshot`/`revert`, `mine`, `increase_time`, `get_logs`. |
| `psv.chain` | `TokenView` — reads balances, `authorizationState`, `AuthorizationUsed` logs; builds `transferWithAuthorization` / admin calldata. `SettlementTruth` — the on-chain ground truth of one payment. |
| `psv.payloads` | EIP-3009 `TransferWithAuthorization` signing (independent of any x402 SDK). |
| `psv.sut` | `SutAdapter` ABC + `HttpSutAdapter`. The contract every SUT must satisfy: `quote` / `pay` / `status`. |
| `psv.reference_sut` | A bundled, faithful SUT used to exercise the harness. Settlement is confirmed by **watching the `Transfer` event** — deliberately the SC1-vulnerable pattern. |
| `psv.divergence` | Compares ground truth to SUT belief → `Divergence` (kind + severity). |

## The SUT adapter contract

Tests talk to the SUT **only** through these three calls, so the same suite runs
against the reference SUT, a future in-house system, or a third party:

- `POST /quote` → `{ order_id, amount, payTo, asset, network, extra:{name,version} }`
- `POST /pay`   `{ order_id, authorization:{ from,to,value,validAfter,validBefore,nonce,signature } }`
  → `{ order_id, submitted_tx, settled }`
- `GET  /status/{order_id}` → `{ order_id, paid, resource, submitted_tx }`

`settled`/`paid` express the system's **belief**. The harness never trusts it —
it cross-checks against the chain.

## Why the oracle reads `AuthorizationUsed`, not `Transfer`

Settlement truth is derived from the **balance delta** plus the
`AuthorizationUsed(authorizer, nonce)` event and the on-chain `authorizationState`
mapping — none of which change when a token's *transfer* event signature drifts.
That independence is what lets the oracle stay correct while a `Transfer`-watching
SUT goes blind (see SC1). If the oracle itself relied on `Transfer`, it would be
just as blind as the system it audits.

## Determinism

Every on-chain test runs inside an Anvil `evm_snapshot` taken before the test and
reverted after (`chain_snapshot` fixture), so tests are order-independent and
repeatable. The token deploys to a deterministic address (first deployer nonce),
so addresses are stable across runs.

## Testability without a chain

`RpcClient` takes an injectable transport and the confirmer takes an injectable
log fetcher, so the harness's decision logic — divergence grading, RPC request
shape, ABI calldata, event-drift blindness — is fully unit-tested offline with no
Anvil. On-chain tests (`-m onchain`) add the real end-to-end confirmation.

## Phasing

Phase 0 (this foundation) delivers the harness, the reference SUT, a green
happy-path, and the first high-damage case (SC1). Later phases add fault
tolerance & settlement edge cases, multi-token quirks, security/game-theory, and
load — each gated behind markers so the default run stays fast and chain-free.
