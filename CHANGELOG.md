# Changelog

All notable changes to psv are documented here. The format loosely follows
[Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added

- Versioned, runtime-attested USDC/Base and EURC/Base rails with a read-only
  `rail-drift` command and scheduled drift observation. Uncalibrated public
  rails fail closed and signing remains disabled for every public rail.
- Exact reconciliation evidence: chain, canonical block hashes, receipt,
  transaction/log identity, authorization nonce, proxy/code identity, balances,
  required amount, received amount, and finality policy.
- Reconciliation report contract 2.0 and run-record contract 1.1, both backed by
  checked-in JSON Schemas. Run records use collision-safe exclusive creation and
  an integrity-checked JSONL journal.
- Central fail-closed outbound safety policy for the reference SUT. It validates
  the configured and observed chain, local/test allowlist, addresses, deployed
  token code, exact payer, and exact amount before signing or submission.
- Strict HTTP SUT wire parsers and live loopback calibration through Uvicorn,
  including malformed responses, timeouts, readiness, and teardown.
- Strict, bounded JSON-RPC response validation and normalized `RpcError` failures.
- Concurrent facilitator-account load profiles for ramp, spike, soak,
  breakpoint, and recovery, with bounded error samples and machine-readable
  attempted/successful throughput and correctness counts.
- Enforced machine-readable support matrix and continuous public-repository
  sanitation checks.
- Foundry tests for zero-address, malformed-signature, `v`, and low-`s` guards.
- Reproducible CI inputs, full-SHA GitHub Action pins, Python 3.11-3.14, dependency
  audit, package/twine/wheel smoke tests, Forge tests, Solidity linting, and
  automated dependency update policy.

### Changed

- `psv reconcile` now requires `--tx-hash`, `--log-index`, and positive
  `--required-amount`. Aggregate balance deltas alone are no longer accepted as
  proof of one settlement.
- Report version 1 consumers must migrate to report version 2's nested evidence
  object. Run-record version 1 consumers must accept schema version 1.1.
- Settlement identity is `(chain, asset, transaction hash, log index)`; recovered
  order IDs bind the complete identity instead of a hash prefix.
- ABI, address, nonce, signature, numeric, price, finality, CLI, path, and URL
  inputs now validate exact domains and fail closed.
- Run records are described as integrity-checked, not tamper-evident. Any report,
  output, or audit-record write failure yields exit 2.

### Fixed

- String and numeric booleans can no longer invert SUT payment belief.
- Confirmation is bound to the submitted transaction, receipt, exact logs,
  nonce, payer, payee, and amount; unrelated or same-block transfers cannot
  confirm an order.
- Underpayment is reachable through the real CLI and returns a failing verdict.
- Removed/reorged logs, chain mismatches, proxy drift, malformed bytecode, hostile
  RPC shapes, multi-log transactions, duplicate logs, and identifier prefix
  collisions cannot be silently accepted.
- The calibration token rejects zero endpoints, zero recovered signers, invalid
  `v`, and high-`s` signatures.

## [0.1.0] - 2026-07-09

First release of the system-level x402 verification harness with an independent
chain-truth oracle, divergence detector, local reference SUT, reconciliation,
reorg, quote-option, token-quirk, security, differential, and load scenarios.
All transaction-producing tests use local Anvil funds only; the CLI is
read-only.
