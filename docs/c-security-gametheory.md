# C / N — Security & game-theory at the system level (Phase 4)

**Status:** C0 reproduced offline + on-chain; N10 reproduced offline.

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

## Defenses `psv` can verify

- Issue **unguessable** order/session ids (≥ 64 bits of entropy); never sequential.
- Verify the authorization's EIP-712 **domain chainId** equals the system's chain
  before submitting (and rely on the token's on-chain domain binding as backstop).
- Scope settlement verification to the **exact asset contract address**; maintain
  an allow-list of accepted tokens.
- Treat the claimed payer address as untrusted until the signature recovers to it
  under the correct domain.
