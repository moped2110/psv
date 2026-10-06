# Changelog

All notable changes to psv are documented here. The format loosely follows
[Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [0.5.0] — 2026-10-06

### Added

- **Pending pay outcome.** `PayResult.outcome` (`psv.sut.PayOutcome`) maps a pay answer to
  `settled`, `pending` or `unsettled`. A failed settle that names its transaction and carries
  x402's non-terminal `settlement_pending` (CORE §9) is `pending`: the transfer may still
  land, so the order is neither paid nor unpaid until the named transaction is reconciled.
  Before, it was indistinguishable from any other unsettled answer. `settlement_pending`
  without a hash stays `unsettled` (nothing to reconcile; x402 requires
  `unexpected_settle_error` there), and a `settled` answer carrying it is rejected.
- **PSV-I-002** (P0, Anvil): the resource server's single retry after `settlement_pending`
  must not broadcast a second settlement. The reference SUT now answers a broadcast whose
  receipt cannot be fetched (`receipt_tries`, default 50 polls) with `settlement_pending`
  and the tx hash instead of an error. With `idempotent_pay` its retry waits on that hash
  with the original authorization, also after the quote expired; the vulnerable default
  broadcasts the authorization again, the second transaction reverts, and the order is
  booked unpaid although the first one paid: a silent loss the chain-truth oracle catches.

### Changed

- The pay wire's optional `reason` is now parsed: a bounded non-empty string or null.
  Any other type fails closed like the rest of the contract.

### Documented

- **Install from GitHub, not PyPI.** The PyPI name `psv` belongs to an unrelated CSV parser
  (<https://pypi.org/project/psv/>), so README's `pip install "psv[mcp]"` installed the
  wrong package. README's Install and MCP sections now use
  `pip install "psv[mcp] @ git+https://github.com/moped2110/psv@v0.5.0"`, and the missing
  eth-account hint points at README "Install" instead of `pip install psv[chain]`.
- `docs/sc1-abi-drift.md` cites the Starknet (d6d2c580, #3126) and Hedera (Phase 4)
  settle-semantics clarifications at `x402-foundation/x402@cb0ec5b`: on-chain effect, not
  receipt status, decides, and an unconfirmed broadcast is `settlement_pending` with its
  hash. Both match psv's reconcile-from-chain stance.

### Not done (follow-up)

- New rails from the 2026-10 upstream DEFAULT_ASSETS (Sei `eip155:1329`/`1328`, Monad
  testnet `eip155:10143`, Arc `eip155:5042`/`5042002`, Celo USDT and USAT). No calibration
  data exists yet; a rail is registered only with a one-block attestation captured by
  `tools/capture_rail_attestation.py`.

## [0.4.0] — 2026-10-06

### Added

- Opt-in `psv.pqc` verification for version 2 facilitator receipts. Strict verification uses
  ECDSA-P256-SHA256 and ML-DSA-65 as an AND-composition, binds both algorithm and key IDs, and
  reports `Unverifiable Receipt` or `Naked Receipt` findings without changing the existing path.
  The addition is backward-compatible: `PSV_PQC` defaults to off, and the disabled verifier
  returns the original receipt bytes without parsing or serialization.
- Primary `cryptography`/OpenSSL 3.5 provider (`psv[pqc]`) with runtime capability detection,
  plus an isolated optional `psv[pqc-oqs]` backend. Both extras require `cryptography>=50`,
  the floor the CI lock already enforces for PYSEC-2026-3552, so a published install cannot
  resolve a cryptography release with that advisory. NIST ACVP ML-DSA-65 verification fixtures
  cover the primary backend independently of implementation-generated material.
- A signed receipt-v2 interop vector (`tests/pqc/vectors/receipt-v2-interop.json`, test keys
  only) that x402-conformance carries byte-identically and verifies with its own
  canonicalization, so the two implementations cannot drift apart unnoticed. Its `rejected`
  receipt texts (floats, `NaN`, repeated and non-ASCII member names) must be refused by both
  with the same error.
- Versioned receipt-v2 JSON Schema and `docs/pqc-receipt-v2.md`, including downgrade handling,
  key references, wire overhead, latency-measurement guidance, migration, rollback, and
  threat-model limits.

### Changed

- **The three 2026-08 rails are calibrated from the chain, and their EIP-712 domains are
  solved, not asserted** (`usdc-celo`, `usdc-celo-sepolia`, `usdt0-flare`; closes the
  0.3.0 known gap). They were registered from upstream's table and then read: one
  finalized block each, runtime-code hash, proxy slot, implementation address and its
  code hash. The domains were recovered by reproducing each contract's own
  `DOMAIN_SEPARATOR()`. That distinction paid on Flare, where the contract exposes
  **no `version()` at all** — the attested `"1"` is not readable from the token and
  could only be found by solving for it. Both USDC deployments answer on Circle's proxy
  slot; Flare's `USD₮0` is an ERC-1967 proxy, probed rather than assumed, which is a new
  reviewed `proxy_kind`.
- **`proxy_kind` is validated against a reviewed set.** The field is load-bearing:
  `"none"` makes `check_rail_drift` skip implementation verification entirely, and a
  proxy's runtime-code hash does *not* change when its implementation is swapped —
  so a wrong `"none"` blinds the drift check to the one change it exists to catch.
  Validation cannot stop that choice being wrong; it stops the value being a typo, and
  makes a new kind a deliberate addition.

### Fixed

- **`psv.__version__` comes from the installed distribution.** It was hard-coded to
  `"0.1.0"` and never moved through 0.2.0 and 0.3.0 — and run records copy it into
  `tool.version`, so every record since 0.1.0 named the wrong release of the tool that
  produced it, and the MCP server advertised the same stale version. It is now read
  from package metadata (one source of truth: `pyproject.toml`), with a `0.0.0+source`
  fallback for an uninstalled source tree; the reference SUT's FastAPI app reports it
  too. A test pins `__version__` to the installed metadata.

### Documented

- `docs/rails.md` lists every registered rail, and a test keeps that list in step with
  the registry. The README's list of extras now includes `mcp` and `core`.
- The released 0.3.0 section below is restored to what was tagged. The calibration
  above had been written into it after the tag, which made the changelog claim the
  `v0.3.0` artifact contained work it does not.

### Security

- **CI lock refreshed so `pip-audit --strict` is clean again (replaces Dependabot #13 by hand).**
  The weekly supply-chain job on `main` was red on 21 advisories in four
  transitive packages: `httpx2` 2.10.0 (PYSEC-2026-3846/-3848/-3849), `urllib3` 2.7.0
  (PYSEC-2026-4175..4177), `pyjwt` 2.13.0 (PYSEC-2026-4140..4152, CVE-2026-102275) and
  `multidict` 6.7.1 (CVE-2026-104874). Each now has a security floor in
  `requirements/ci.in`, next to the existing `cryptography` and `aiohttp` floors, so the gate
  cannot quietly regress. The rest of the lock was refreshed with `--upgrade` rather than by
  raising the floors in `pyproject.toml`, which is what Dependabot proposes and what K3-1
  declined: a floor in the `chain`/`sut` extras is a permanent constraint on every consumer,
  and the lock reaches the same versions without one.
  Notable moves in the lock: `web3` 7.16.0 → 8.0.0, `eth-account` 0.13.7 → 0.14.0,
  `eth-abi` 5.2.0 → 6.0.0 (now a final release, not the beta corrected in K3-1), `mcp`
  2.0.0 → 2.3.0, `uv` 0.12.3 → 0.12.23. `eth-account` 0.14 still exposes the private
  `eth_account.messages._hash_eip191_message` that `psv.payloads` imports, so the
  published floors (`web3>=7.0`, `eth-account>=0.13`) stay as they are and still resolve
  together with x402-conformance.

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

### Known gap

- **The three new rails' EIP-712 domains are asserted, not solved.** `domain_name` and
  `domain_version` for `usdc-celo`, `usdc-celo-sepolia` and `usdt0-flare` come from
  upstream's default-asset table rather than from each contract's `DOMAIN_SEPARATOR()`.
  EIP-712 hashes the domain string byte-exactly, so `USD₮0` is one character away from a
  domain no signature will ever match. `calibrated=False` keeps the claim from being
  load-bearing today; solve them with `tools/capture_rail_attestation.py` before any of
  these is calibrated.

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
