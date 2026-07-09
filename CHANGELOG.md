# Changelog

All notable changes to psv are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [0.1.0] — 2026-07-09

First release. A system-level verification harness for complete x402 payment
systems: it holds two independent records of each payment — on-chain ground truth
read straight from the token, and the system-under-test's belief — and grades the
gap between them.

### Added
- **Divergence detector** (`psv.divergence`): grades chain-truth vs. SUT-belief
  into consistent-paid / consistent-unpaid / silent-loss / phantom-credit /
  underpaid-credit, marking the money-losing asymmetries CRITICAL. Property-based
  tests (Hypothesis) prove the grading against an independent truth table across
  the input space.
- **Chain-truth oracle** (`psv.chain`): reads balances, EIP-3009 nonce state and
  the drift-proof `AuthorizationUsed` event by hand-rolled ABI, so settlement
  truth survives `Transfer`-event signature drift. Slot encoders reject
  out-of-range uint256 / oversized address / bytes32 (large-value guard).
- **Controllable chain** (`psv.anvil`): snapshot/revert, mine, time travel and
  `eth_getLogs` over Anvil, with transport faults normalised to `RpcError`.
- **Reference SUT** (`psv.reference_sut`): a faithful miniature payment system —
  quote, on-chain settle, event-watching confirmation, idempotent re-pay — used
  as the calibration target. Asset-aware reconciliation attributes credits per
  token (multi-asset).
- **Scenario modules**: reconciliation (D3 ledger rollback), quote-as-option
  (G3), reorg invalidation (P-03), token quirks / fee-on-transfer (P-05),
  security checks incl. cross-chain replay & id predictability (P-06), and a load
  profile harness (P-07).
- **EIP-3009 signing** (`psv.payloads`): independent EIP-712 signing proven
  domain-bound (cross-token replay resistance).
- **Read-only reconciliation CLI** (`psv`), stdlib-only.
- **CI**: offline lint/format/type/coverage gate on Python 3.11–3.13, plus an
  on-chain gate that spins up Anvil, deploys the token and runs `-m onchain`.

### Security / invariants
- No custody, no payment service, no advice — a test tool only. On-chain tests
  use Anvil's public dev keys and local test funds; never mainnet.
