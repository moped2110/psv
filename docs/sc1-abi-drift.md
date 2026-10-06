# SC1 — Contract / event drift causes silent payment loss

**Class:** supply-chain / upgrade risk. **Severity:** critical. **Status:**
reproduced end-to-end against the bundled reference SUT.

## The failure

Many payment systems confirm settlement by **watching a token event** — they
scan the chain for the merchant's incoming `Transfer` and mark the order paid.
That filter is pinned to one event signature, i.e. one `topic0`
(`keccak256("Transfer(address,address,uint256)")`).

A token can change its settlement event signature **without changing its
address** — a proxy implementation upgrade is the textbook case. The moment the
emitted event becomes, say,
`TransferV2(address,address,uint256,bytes32)` (a different `topic0`), the
system's filter matches nothing. Funds still move on-chain exactly as before, but
every payment now looks **unpaid**. The customer is debited and receives
nothing, and — because the system simply sees "no settlement event" — **no error
is raised**. The loss is silent.

## How `psv` reproduces it

`UpgradeableMockUSDC` is a faithful EIP-3009 token (on-chain signature
verification + nonce tracking) with one extra control: an admin
`setEventMode(uint8)` that switches which event a settlement emits, **in place**:

- mode 0 → `Transfer(address,address,uint256)` (legacy)
- mode 1 → `TransferV2(address,address,uint256,bytes32)` (drifted)

Same contract address, same storage, same balances, same EIP-712 signing domain
— only the emitted settlement event changes. This is a true in-place upgrade of
the event a downstream indexer relies on, with none of the storage-layout risk of
a real delegatecall proxy.

The test (`tests/test_sc1_abi_drift.py`) runs two phases against the reference
SUT, which confirms settlement via the legacy `Transfer` `topic0`:

1. **Baseline (mode 0).** A payment settles; the SUT registers it; chain truth
   and belief agree (`CONSISTENT_PAID`). This proves the confirmer works, so the
   next phase is a real regression rather than a never-working path.
2. **Drift (mode 1).** A second payment moves funds on-chain identically
   (verified via balances + `authorizationState` + `AuthorizationUsed`), but the
   SUT's `Transfer` filter returns nothing and it reports the order **unpaid**.

The independent chain-truth oracle sees the money move; the SUT does not; the
divergence detector raises a critical **`SILENT_LOSS`**.

## Why black-box conformance can't catch this

A protocol conformance tester only observes the endpoint's HTTP responses. After
the drift the endpoint behaves "correctly" by its own lights — it genuinely
believes no payment arrived and returns `402`. Nothing in the protocol exchange
is malformed. Only an oracle that reads the chain *independently of the event the
system trusts* can see that a settlement really happened. That independence is
the harness's reason to exist.

## Upstream confirmed the class (x402#2385 / #2727 / #3032, 2026-08)

SC1 is not a scenario invented for this harness. In August 2026 the x402 SDKs
fixed the same reasoning error in all three languages — TypeScript
([#2385](https://github.com/x402-foundation/x402/pull/2385)), Go
([#2727](https://github.com/x402-foundation/x402/pull/2727)) and Python
([#3032](https://github.com/x402-foundation/x402/pull/3032)). From the TypeScript
PR:

> `settleEIP3009` currently treats `receipt.status === "success"` as proof of a
> successful transfer. The receipt's status only tells us the tx did not revert;
> it does not tell us that the expected ERC-20 `Transfer` was emitted from the
> expected token contract with the expected `(from, to, value)`.

The fix adds `ErrTransferEventMismatch`
(`invalid_exact_evm_transfer_event_mismatch`) so a transaction that succeeded but
emitted no matching `Transfer` is reported as a settlement *failure* rather than
a success.

That is the same premise SC1 attacks, approached from the other side. Upstream's
bug was trusting the receipt and never checking the event; SC1 is trusting the
event and having it change underneath. Both reduce to the same thing: **one
signal is being treated as proof of settlement, and the signal can be true while
the payment is not.** Upstream now checks receipt *and* event; SC1 shows that
even the event alone is not enough once a token can change what it emits, which
is why the defenses below ask for independent signals rather than a better single
one.

Two later scheme specs state the same rule for their chains (`x402-foundation/x402`
@ `cb0ec5b`, reviewed 2026-10-06):

- **Starknet** (`specs/schemes/exact/scheme_exact_starknet.md`, d6d2c580, #3126):
  settlement succeeds only if the receipt is `SUCCEEDED` **and** it emits exactly the
  expected payer-scoped `Transfer`; a non-reverting transaction without it is a failure,
  because the call runs inside the payer's own account. A consumed nonce is terminal,
  success is reported only for a transaction the facilitator broadcast itself, and when
  confirmation cannot be established the answer is the non-terminal `settlement_pending`
  with the hash, never a re-broadcast of the same authorization.
- **Hedera** (`specs/schemes/exact/scheme_exact_hedera.md`, Phase 4): the outcome comes
  from the consensus record, child records included, not from the receipt status: "a
  `SUCCESS` receipt with no transfer is a failed settlement". An unobtainable record is
  `settlement_pending` with the transaction id.

Both are psv's reconcile-from-chain stance: the effect on chain decides, not the
signal that reports it. psv grades a `settlement_pending` answer that names its
transaction as a pending outcome (`psv.sut.PayOutcome.PENDING`) and the retry that
follows it in PSV-I-002.

## Defenses a real system should adopt (and `psv` can verify)

- Confirm settlement on **multiple independent signals** (balance delta and/or
  `authorizationState`), not a single event `topic0`.
- Treat "expected settlement not observed within N blocks" as an **alert**, not a
  silent `unpaid`.
- Pin and monitor the token implementation; alarm on proxy-upgrade events.
- Run a periodic **reconciliation** job comparing internal ledger to chain
  balances (this is the D-class scenario, a planned next case).
