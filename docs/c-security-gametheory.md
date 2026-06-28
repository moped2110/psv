# C / N — Security & game-theory at the system level (Phase 4)

**Status:** C0 reproduced offline + on-chain; N10, N15, N16 reproduced offline.

## C0 — Cross-chain signature replay

An EIP-3009 authorization is bound to a `chainId` through its EIP-712 domain
(`name`, `version`, `chainId`, `verifyingContract`). An authorization signed for
chain A, replayed against a system on chain B, recovers to a **different**
address — so the token's on-chain signature check fails and the settlement
reverts. The danger is a system that submits first and asks later: it wastes a
reverting transaction and, worse, a system that *trusts the claimed `from`*
without verifying the domain binding could be tricked.

`psv.security_checks.authorization_binds_to_chain` recomputes the recovery under
the system's own chain id and confirms it matches the claimed `from` — a
pre-flight reject for replayed authorizations. On-chain, the token's domain
separator (built from `block.chainid`) makes the replay revert: no funds move,
the nonce is untouched. (Offline: `tests/test_security_unit.py`; on-chain:
`tests/test_c0_cross_chain_replay.py`.)

## N10 — Fake-token / whitelist bypass

Settlement must be verified against the **expected asset contract**, not "any
`Transfer` to the merchant". An attacker can deploy a worthless token, transfer it
to the merchant, and — if the system scans events without scoping by token address
— be credited for free. `psv.security_checks.asset_matches` is the guard; the
reference confirmer already scopes its `eth_getLogs` to the configured token
address, so a fake-token transfer is ignored. (Offline: `tests/test_security_unit.py`.)

## N15 — Session / order-id predictability

If order ids are sequential or short (`ord_1`, `ord_42`), an attacker enumerates
them and tries to claim another customer's paid resource. Ids must carry enough
unpredictable entropy. `psv.security_checks.sufficient_id_entropy` checks the
non-prefix body is high-entropy hex; the reference SUT uses `secrets.token_hex(8)`
(16 hex chars) and passes, while sequential schemes fail. (Offline:
`tests/test_session_unit.py`.)

## N16 — Asset is an EOA (silent no-op / payment bypass)

A close cousin of N10, but more insidious. The EVM does **not** revert when a
function is called on an address with no contract code: `eth_call` returns empty
data and an on-chain `transferWithAuthorization` against an EOA "succeeds" —
status 1 — while moving nothing and emitting no `Transfer`. A system that points
its `asset` at (or accepts a payment claiming) an EOA, and skips an `eth_getCode`
pre-flight, settles a **silent no-op**: it believes it was paid, the chain shows
nothing moved. The harness's independent oracle catches it — `SettlementTruth.funds_moved`
is `False` (no nonce burned, no balance delta) while the SUT reports success: a
PHANTOM_CREDIT divergence.

`psv.security_checks.asset_is_deployed_contract` is the guard: an `eth_getCode`
result with no bytecode → reject the asset before verifying the signature or
settling. This mirrors the x402 SDK's `asset_not_deployed_contract` check
(upstream x402#2554), which added exactly this pre-flight to the EVM facilitator's
`verify` for EIP-3009, Permit2 exact, and Permit2 upto. (Offline:
`tests/test_eoa_asset_unit.py`.)

## Defenses `psv` can verify

- Issue **unguessable** order/session ids (≥ 64 bits of entropy); never sequential.
- Verify the authorization's EIP-712 **domain chainId** equals the system's chain
  before submitting (and rely on the token's on-chain domain binding as backstop).
- Scope settlement verification to the **exact asset contract address**; maintain
  an allow-list of accepted tokens.
- **Pre-flight `eth_getCode` on the asset**: reject an asset with no bytecode (an
  EOA) before settling — a call to an EOA never reverts, so settlement would be a
  silent no-op.
- Treat the claimed payer address as untrusted until the signature recovers to it
  under the correct domain.
