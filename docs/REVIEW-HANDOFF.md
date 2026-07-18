# Technical review handoff

This document is the shortest reliable route through a security and correctness
review of the current `psv` working tree. Review the implementation and tests,
not this guide alone. The machine-readable shipped/planned boundary is
`support-matrix.json`.

## Mission and non-goals

`psv` verifies whether a complete x402 payment system's belief agrees with
independently observed chain truth. It covers settlement attribution, RPC and
adapter failures, reconciliation, reorgs, token quirks, and opt-in load profiles.

It is not an x402 protocol-conformance suite, wallet, custodian, payment service,
settlement facilitator for production funds, legal opinion, or financial advice.
The bundled reference SUT exists to calibrate the verifier and reproduce damage
cases; it is not a production server.

## Non-negotiable value-safety invariant

- Reconciliation and rail-drift commands are read-only.
- No bundled path may sign for or transfer mainnet value.
- The sole signing boundary is
  `ReferenceSut._submit_settlement`; before calldata construction, signing, or
  broadcast it calls `SettlementSafetyPolicy.require_safe_submission`.
- The policy has no runtime override. It permits only an explicit local/testnet
  chain allowlist and binds configured/live chain ID, deployed token code,
  non-zero payer/payee/token, authorization recipient, and exact quoted amount.
- Unknown/unreachable chains and every EVM mainnet fail closed.

Review any change that weakens this sequence as security critical.

## Architecture and data flow

```text
SUT quote/pay/status ----> strict adapter ----> SUT belief
                                                  |
untrusted JSON-RPC -> strict decoder -> rail attestation -> pinned evidence
                                                  |
                           exact divergence comparison
                                      |
                    report v2 + run record v1.1
```

The SUT and RPC endpoint are independent untrusted inputs. A positive settlement
requires one exact `(chain_id, token, transaction_hash, log_index)` identity plus
a canonical receipt/block, exact Transfer and AuthorizationUsed logs, nonce
state, payer/payee, amount, token/proxy code identity, and parent/settlement block
balances. The verifier rejects same-block attribution races and rechecks block
identity after observation. Aggregate `latest` balance deltas never prove a
settlement.

Trust boundaries worth reviewing first:

1. `psv.safety` before any reference-SUT signing.
2. `psv.anvil.RpcClient` at the JSON-RPC envelope/result boundary.
3. `psv.sut.HttpSutAdapter` at the HTTP/JSON SUT boundary.
4. `psv.rails.reconcile_live` where untrusted evidence becomes a verdict.
5. `psv.report` and `psv.run_record` where evidence becomes an audit artifact.

## Public contracts

### SUT adapter

- `quote()` returns a normalized order ID, positive decimal-string amount, exact
  payee/asset addresses, positive EVM CAIP-2 network, and token domain.
- `pay(order_id, authorization)` binds the response order ID to the request and
  returns an optional exact transaction hash plus a literal settlement boolean.
- `status(order_id)` URL-encodes the exact ID and returns literal paid/resource/
  transaction fields.
- Duplicate JSON keys, oversized bodies, permissive booleans/numbers, mismatched
  order IDs, malformed hashes/addresses, and unsafe paths are rejected.

### Reconciliation report v2

`psv.report.ReconReport` emits the contract in
`psv/schemas/reconciliation-report-v2.schema.json`. It includes stable reason
codes, rail/payment identity, finality and settlement blocks, receipt/log
identity, token/proxy fingerprints, nonce evidence, exact balances and amounts,
and the graded divergence. Exit codes are `0` for consistent, `1` for a critical
divergence, and `2` for usage/RPC/evidence/output/audit failure.

### Run record v1.1

`psv.run_record` emits the packaged `run-record-v1.1.schema.json`. RPC URLs are
reduced to scheme and host, secret-like inputs are omitted, record files are
created exclusively, and the JSONL journal uses one append operation per entry.
`runId` is a canonical SHA-256 integrity checksum. It detects accidental changes;
it is not signer authenticity and cannot defend against an attacker who can
rewrite both the artifact and checksum.

## Rail attestation

| Rail | Class | Current behavior |
|---|---|---|
| `mock-anvil` | local | Ephemeral code plus pinned interface; signing remains safety-gated. |
| `usdc-base` | mainnet | Calibrated read-only proxy/code identity at a reviewed block. |
| `eurc-base` | mainnet | Calibrated read-only proxy/code identity at a reviewed block. |
| `jpyc-polygon` | mainnet | Uncalibrated; live reconciliation fails closed. |

`RailAttestation` binds authoritative sources, review version/date, interface,
network class, decimals/domain, reviewed block/hash, proxy kind/slot,
implementation address, and runtime-code fingerprints. `rail-drift` probes the
interface and code identity at a finality block without constructing a
transaction. Calibrated mainnet rails require the full pinned proxy identity.

## Production file and function map

Every Python function, method, async function, and nested function below has a
non-empty behavior docstring. `tools/check_function_docs.py` enforces that rule
over `src/` and `tools/`, including itself.

| File | Functions and review purpose |
|---|---|
| `psv/anvil.py` | `_reject_*`, `_bounded_json`, `_quantity`, `_data`, `_hash`, `_address`, `_block_param`, `_validate_log`, and `_validate_receipt` strictly decode hostile RPC data; `_urllib_transport.send` bounds HTTP; `RpcClient` owns calls, Anvil controls, pinned reads, logs, code, broadcast, and receipts; `AnvilProcess` owns local lifecycle. |
| `psv/chain.py` | ABI word/topic helpers encode exact values; `TokenView` reads pinned balances/nonces/logs and builds validated mock calldata; `SettlementTruth.funds_moved` derives attributable movement. |
| `psv/cli.py` | CLI parsers validate addresses, hashes, uint256, timeout, URL, and paths; `run_reconcile` builds a report; command handlers write bounded output and integrity records with stable exit codes. |
| `psv/differential.py` | `run_differential` compares independent SUT adapters; `DifferentialResult.has_finding` exposes disagreement. |
| `psv/divergence.py` | `detect_payment_divergence` grades consistent, silent-loss, phantom-credit, and underpaid outcomes; `settlement_truth_from_balances` constructs exact deltas. |
| `psv/load.py` | Result properties compute correctness, latency, and throughput; `run_profile`, `run_staged_profile`, facilitator routing, offsets, and `standard_profile` drive bounded concurrent stages. |
| `psv/payloads.py` | EIP-712 domain/message/digest helpers, `EvmSigner`, `Authorization.as_dict`, and `sign_authorization` create local/test authorization inputs. |
| `psv/quote_option.py` | Exact amount/tolerance helpers, `option_value`, `quote_is_stale`, and `simulate_attacker` model quote free-option exposure without float comparisons. |
| `psv/rails.py` | Config validators enforce attestation/read-only invariants; lookup and drift functions verify rail identity; evidence helpers bind blocks/code/proxy/logs; `reconcile_live` derives a single atomic verdict. |
| `psv/reconciliation.py` | Strict hex/quantity/address/topic decoding constructs collision-free `SettlementIdentity` and `OnChainCredit`; `find_unreconciled` rejects removed/conflicting/cross-asset credits. |
| `psv/reference_sut/confirmer.py` | Topic/quantity helpers and `EventWatchingConfirmer` bind receipt, fetched log, transaction, payer/payee, amount, and authorization nonce. Its fixed event signature is an intentional SC1 calibration weakness. |
| `psv/reference_sut/server.py` | `ReferenceSut` implements quote/pay/status, ledger backup/restore/reconciliation, and safety-gated local/test settlement; `create_app` exposes the adapter contract. |
| `psv/reorg.py` | `confirmations`/`is_final` implement exact finality arithmetic; checkpoint/revert helpers simulate deterministic Anvil reorgs. |
| `psv/report.py` | `ReconReport` builds, validates, and renders report v2; document validation and `exit_code` enforce contract invariants. |
| `psv/run_record.py` | Redaction/hash helpers build and verify records; exclusive writes and append-only journal handling preserve concurrency-safe traceability; journal verification reports corruption. |
| `psv/safety.py` | Address/chain/code/amount validators feed the central, non-overridable pre-signing policy. |
| `psv/security_checks.py` | Signer recovery, chain-domain binding, asset matching, order-ID entropy, and deployed-contract checks provide independent security assertions. |
| `psv/sut.py` | Strict quote/pay/status models and parsers plus bounded, closeable `HttpSutAdapter` implement the SUT trust boundary. |
| `psv/token_quirks.py` | Exact decimal/uint256 conversion, fee math, sufficiency, and underpayment helpers avoid float and coercion errors. |
| `tools/check_function_docs.py` | Discovers production Python, reports missing docstrings, counts functions, and supplies the CI-facing gate. |
| `tools/check_public_repo.py` | Enumerates public files and rejects local paths, private context, and non-public attribution. |
| `tools/validate_support_matrix.py` | Validates scenario structure, status semantics, unique IDs, and registered pytest selectors. |
| `onchain/src/UpgradeableMockUSDC.sol` | NatSpec documents constructor, admin controls, mint, domain separator, EIP-3009 settlement, and canonical signature recovery; configurable event drift and transfer fee reproduce SC1/T-class damage. |

## Test map

Tests deliberately keep scenario-oriented names instead of boilerplate function
docstrings.

| Area | Test files / scenarios |
|---|---|
| Strict RPC and adapter boundary | `test_adapter_and_rpc_unit`, `test_rpc_strict_unit`, `test_rpc_robustness`, `test_sut_strict_unit`, `test_http_adapter_live`, `test_session_unit` |
| ABI and numeric domains | `test_abi_encoding_property`, `test_abi_strict_unit`, `test_numeric_domains_unit`, `test_payloads_unit`, `test_token_quirks_unit`, `test_quote_option_unit` |
| Atomic reconciliation and reports | `test_reconciliation_unit`, `test_reconciliation_property`, `test_reconciliation_multiasset`, `test_reconcile_negative_paths`, `test_rails_unit`, `test_cli_unit`, `test_run_record_unit` |
| Safety and reference SUT | `test_reference_sut_safety`, `test_confirmer_unit`, `test_server_unit`, `test_eoa_asset_unit`, `test_security_unit` |
| System damage cases | happy path, SC1 drift, C0 replay, D3 restore, G3 option, reorg invalidation, idempotency, EOA asset, fee-on-transfer, delay, stuck mempool, and facilitator crash test files |
| Differential and properties | `test_differential_unit`, `test_divergence_unit`, `test_divergence_property`, `test_edge_cases_unit`, `test_eurc_domain` |
| Opt-in load/on-chain | `test_load_unit`, `test_load_throughput`, and all tests marked `onchain` or `load` |
| Repository contracts | `test_support_matrix`, `test_public_repo_sanitation`, `test_function_docs` |
| Solidity | Seven direct Foundry security tests cover canonical signatures, zero endpoints/recovery, recovery IDs, low-s enforcement, and malleability; replay, expiry, event drift, and fee behavior are exercised by Python/Anvil scenarios. |

## CI and local verification matrix

- Offline pytest with coverage at least 90% on Python 3.11, 3.12, 3.13, and 3.14.
- Ruff lint/format, mypy strict core, function-doc gate, public sanitation, support
  matrix validation, and Solhint.
- Hash-locked install, dependency consistency/audit, sdist/wheel build, Twine
  validation, packaged schemas, and clean core/chain/SUT wheel smokes.
- Foundry format/build/test plus local Anvil on-chain pytest.
- Scheduled, read-only USDC/EURC Base rail-drift observations.
- Load tests are opt-in and excluded from the normal offline gate.

Suggested review commands:

```bash
python tools/check_function_docs.py
python -m pytest -q --cov --cov-fail-under=90
python -m mypy
python -m ruff check src tests tools
python -m ruff format --check src tests tools
python tools/check_public_repo.py
python tools/validate_support_matrix.py
forge fmt --check --root onchain
forge build --root onchain
forge test --root onchain
```

## Targeted review checklist

- [ ] Prove every path reaches the safety policy before signing or broadcast.
- [ ] Look for a runtime mainnet/test-chain override or implicit chain fallback.
- [ ] Fuzz duplicate fields, oversized/deep JSON, non-canonical quantities,
      bool-as-int values, zero addresses, and order-ID/path confusion.
- [ ] Try to make receipt, selected log, fetched log, nonce, block, and balances
      refer to different transactions or observation windows.
- [ ] Try same-block payer/payee transfers, duplicate log indices, removed logs,
      cross-token logs, failed receipts, proxy drift, and mid-read reorgs.
- [ ] Check exact amount semantics for fee-on-transfer and event/balance mismatch.
- [ ] Validate report/schema cross-fields, URL redaction, record collisions,
      partial writes, journal truncation, and checksum limitations.
- [ ] Confirm the reference confirmer's fixed event signature is isolated as a
      deliberate test weakness and is never used as independent chain truth.
- [ ] Confirm support-matrix shipped/planned statuses match executable tests.
- [ ] Run the function-doc checker after adding any production function.

## Known limits and deferred roadmap

- JPYC/Polygon is registered but deliberately uncalibrated and fails closed.
- Run-record checksums provide integrity detection, not cryptographic authorship.
- The reference SUT is intentionally minimal and contains selectable vulnerable
  modes used to prove that the harness detects damage.
- A separate finality/reorg policy variant, multi-asset settlement races, an SVM
  implementation, further live rails, explicit `upto` semantics, hosted-product
  integration, and a formal responsible-disclosure process remain planned.
- Live rail metadata can change; scheduled drift checks detect rather than
  automatically trust changes. Attestation updates require independent review.
