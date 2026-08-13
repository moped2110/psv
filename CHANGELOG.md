# Changelog

All notable changes to psv are documented here. The format loosely follows
[Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [0.3.0] — 2026-08-13

### Added

- **USDC on Polygon is a calibrated read-only rail (PSV-RAIL-USDC-POLYGON).** `rails.py` is a
  registry, so one more calibrated rail is one more chain a settlement can be verified against —
  and Polygon settles in USDC, not in the rail that was already registered there. Pinned from a
  single finalized block: runtime-code hash, proxy implementation slot, implementation address and
  its code hash. Signing stays disabled, as it is for every reconciliation rail.
- **`tools/capture_rail_attestation.py` — the read that makes calibration reviewable.** A
  calibrated rail claims "this is the contract we reviewed"; typing those values out of a block
  explorer makes that claim unverifiable. The tool performs the read (`eth_chainId`,
  `eth_getBlockByNumber`, `eth_getCode`, `eth_getStorageAt`, `eth_call` — nothing else) through
  psv's own hardened RPC client, so the capture inherits redirect refusal, bounded responses and
  endpoint redaction.
  Its point of view is that the **EIP-712 domain is solved, not assumed**: `FiatTokenV2_2` does not
  necessarily derive its domain separator from the current `name()`, so the tool reads
  `DOMAIN_SEPARATOR()` and reports which candidate name/version actually reproduces it. Copying
  `name()` into `domain_name` would attest to a domain the contract never uses — and no test would
  catch it, because every test would share the assumption. For Polygon USDC the two agree; that is
  a finding, not a premise.

### Changed

- **Rail attestations carry their own review date.** `_attestation()` hard-coded one date for every
  rail, so a rail captured today would have attested to a review that happened weeks earlier. The
  date and version are now per rail, and a test requires the version string to begin with the
  review date so two captures months apart stay distinguishable. The three rails below carry
  2026-08-13 for the same reason: they were taken from upstream's table today, and inheriting
  the July date would have been exactly the claim this change exists to prevent.

- **Rails for the networks x402 added default stablecoins to (x402#3025, #3031):**
  `usdc-celo` (42220), `usdc-celo-sepolia` (11142220) and `usdt0-flare` (14).
  All three are **uncalibrated**, so live reconciliation fails closed until a
  reviewed block, runtime-code hash and proxy implementation are captured from
  the chain independently — the same posture as `jpyc-polygon`. Recording them
  now is the difference between an endpoint quoting a *known-but-uncalibrated*
  rail and an *unknown* one: the first refuses with a reason, the second just
  refuses.

### Documented

- **SC1 is confirmed by upstream, from the other direction.** In August 2026 the
  x402 SDKs stopped treating `receipt.status` as proof of transfer in all three
  languages (x402#2385 TS, #2727 Go, #3032 Python), adding
  `invalid_exact_evm_transfer_event_mismatch` for a transaction that succeeded
  but emitted no matching `Transfer`. Upstream's bug was trusting the receipt and
  never checking the event; SC1 is trusting the event and having it change
  underneath. Both reduce to one signal being treated as proof of settlement when
  that signal can be true while the payment is not. `docs/sc1-abi-drift.md`
  records the connection.

- **All three are now calibrated from the chain, and their EIP-712 domains are solved,
  not asserted.** They were registered from upstream's table and then read: one finalized
  block each, runtime-code hash, proxy slot, implementation address and its code hash.
  The domains were recovered by reproducing each contract's own `DOMAIN_SEPARATOR()`.
  That distinction paid on Flare, where the contract exposes **no `version()` at all** —
  the attested `"1"` is not readable from the token and could only be found by solving
  for it. Both USDC deployments answer on Circle's proxy slot; Flare's `USD₮0` is an
  ERC-1967 proxy, probed rather than assumed, which is a new reviewed `proxy_kind`.
- **`proxy_kind` is validated against a reviewed set.** The field is load-bearing:
  `"none"` makes `check_rail_drift` skip implementation verification entirely, and a
  proxy's runtime-code hash does *not* change when its implementation is swapped —
  so a wrong `"none"` blinds the drift check to the one change it exists to catch.
  Validation cannot stop that choice being wrong; it stops the value being a typo, and
  makes a new kind a deliberate addition.

## [0.2.0] — 2026-08-06

### Added

- **Reorg-aware finality for the reference confirmer (PSV-RD-001).** Finality was measured by depth
  alone, so a settlement with enough confirmations on a fork that was later reorganised away still
  counted as final — a phantom credit, with the EIP-3009 nonce free again. `assess_finality` now
  requires the block to be both deep enough **and** still the canonical one at its height (canonical
  hash equals mining hash). Reorganised away, canonical hash unavailable, unmined or too shallow all
  return `final=False` with a reason. Pure offline decision logic.
- **Asset-scoped reconciliation defeats the multi-asset race (PSV-RD-003).** `find_unreconciled`
  failed closed on logs of a foreign asset, so it could not process a realistic mixed block at all.
  `reconcile_asset_scoped` narrows the snapshot to the expected asset first: a same-block transfer of
  a *different* asset to the same payee is excluded rather than misattributed to this order.
  Cross-recipient transfers within the scoped asset and removed/reorged logs still fail closed.
- **Independent SVM settlement oracle and rail (PSV-RD-004).** `settlement_truth_from_svm_meta`
  reads Solana's `getTransaction` metadata — `err` plus pre/post token balances — without asking the
  system under test anything, and builds the same `SettlementTruth` the EVM path produces. The
  chain-agnostic detectors therefore grade SVM unchanged, for `exact` and `upto` alike. SVM has no
  EIP-3009 nonce, so `err is None` feeds `nonce_consumed`. `RailConfig` is EVM-shaped, so SVM gets a
  parallel read-only `SvmRailConfig`.
- **Over-authorized `upto` (metered) settlements are a divergence (PSV-RD-006).** Under `upto`,
  settling *less* than the authorised maximum is healthy — that is what metering means — and settling
  *more* is the bug. `detect_metered_divergence` mirrors the exact-scheme detector with that sign
  reversed and adds `OVER_AUTHORIZED_SETTLEMENT` as a critical kind. Replay of a spent authorisation
  needs no new kind: it moves nothing on chain and lands as a phantom credit.

- **MCP server (`psv-mcp`, optional `[mcp]` extra).** Exposes the read-only surface —
  `list_rails`, `reconcile_settlement`, `rail_drift` — over the Model Context Protocol, so an
  agent debugging a settlement divergence can ask for the proof from inside an editor instead of
  assembling a long command line. The RPC endpoint comes from `PSV_RPC_URL` and is deliberately
  **not** a tool parameter: which chain a verdict is proven against is a property of the
  deployment, and an agent naming its own node could be pointed at one that lies. An RPC failure
  returns an error, never a verdict.

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

- **CI runs on every branch, not only `main`.** An MCP server sat on a branch for days with no run at
  all; its first run — triggered only because a pull request was opened by hand during a review —
  failed on two transitively vulnerable dependencies. Unopened, that would have arrived as a red
  `main`. x402-conformance made the same change in July after being burned the same way, by
  dependency locks specifically; the lesson had not travelled.
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

### Security

- **The chain-truth transport refuses redirects.** A verdict is worth exactly as much as the chain it
  was read from, and urllib follows redirects by default. A redirecting or hijacked provider could
  move the read to a host the operator never configured — enough to substitute the oracle and turn a
  real divergence into "consistent", or the reverse — and urllib would permit an https to http
  downgrade on the way. A JSON-RPC endpoint has no legitimate reason to redirect, so this refuses
  rather than allowlists. x402-conformance states the same rule in its security policy; psv had
  inherited the concern and not the countermeasure.
- **The RPC endpoint no longer travels back to the caller.** Hosted providers put the API key in the
  URL path, so an endpoint interpolated into an error message carried a credential into every log
  line — and, once the MCP server existed, into an agent's context. Errors now render the endpoint as
  scheme and host only, and the MCP boundary returns a verdict while the detail goes to the log.
- **Security floors on two transitive dependencies.** `cryptography` arrives via the new `mcp` extra
  and `aiohttp` via `web3`; both sat at versions with published advisories (PYSEC-2026-3552,
  PYSEC-2026-3545/3546/3547), which `pip-audit --strict` fails the supply-chain job for.

## [0.1.0] - 2026-07-09

First release of the system-level x402 verification harness with an independent
chain-truth oracle, divergence detector, local reference SUT, reconciliation,
reorg, quote-option, token-quirk, security, differential, and load scenarios.
All transaction-producing tests use local Anvil funds only; the CLI is
read-only.
