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

## Defenses a real system should adopt (and `psv` can verify)

- Confirm settlement on **multiple independent signals** (balance delta and/or
  `authorizationState`), not a single event `topic0`.
- Treat "expected settlement not observed within N blocks" as an **alert**, not a
  silent `unpaid`.
- Pin and monitor the token implementation; alarm on proxy-upgrade events.
- Run a periodic **reconciliation** job comparing internal ledger to chain
  balances (this is the D-class scenario, a planned next case).
