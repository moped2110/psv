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
| `psv.divergence` | Compares ground truth to SUT belief → `Divergence` (`silent_loss` / `phantom_credit` / `underpaid_credit` / consistent, + severity). |
| `psv.reorg` | Reorg/finality math: a payment settled then invalidated by a shallow reorg → `phantom_credit`. (`docs/r-reorg-finality.md`) |
| `psv.token_quirks` | Decimals (fail-loud) + fee-on-transfer: verify on the merchant's **net balance delta**, not the gross `Transfer` event. (`docs/t-token-quirks.md`) |
| `psv.security_checks` | Game-theory guards: cross-chain replay binding (C0), asset-scoping / fake-token (N10), order-id entropy (N15), EOA-asset `eth_getCode` pre-flight (N16). (`docs/c-security-gametheory.md`) |
| `psv.reconciliation` | Backup/restore ledger reconciliation: chain `Transfer`-log diff vs the system ledger → unreconciled credits (D3). (`docs/d3-reconciliation.md`) |
| `psv.quote_option` | Quote-as-free-option: stale-quote value + reprice guard (G3). (`docs/g3-quote-option.md`) |
| `psv.load` | Throughput/latency profiling (`run_profile`): p50/p95/max, error rate, throughput. |

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

Each phase is gated behind pytest markers so the default run stays fast and
chain-free; `-m onchain` / `-m load` add the real end-to-end confirmations.

- **Phase 0 — foundation.** Harness, reference SUT, green happy-path, and the
  first high-damage case: SC1 (event/ABI drift → `silent_loss`).
- **Phase 1 — fault tolerance & settlement edges.** Reorg/finality (R), idempotency
  / exactly-once (re-paid order must settle once), premature-confirmation delay.
- **Phase 2 — reconciliation & quote option.** Backup/restore ledger diff (D3),
  quote-as-free-option reprice guard (G3).
- **Phase 3 — multi-token quirks.** Decimals + fee-on-transfer (T).
- **Phase 4 — security / game-theory.** Cross-chain replay (C0), fake-token /
  asset-scoping (N10), order-id entropy (N15), EOA-asset silent no-op (N16).
- **Phase 5 — load.** Throughput / latency profiling.

All cases are reproduced offline (decision logic) and, where they touch the chain,
confirmed on-chain against Anvil.
