# R — Reorg invalidation & finality (Phase 2)

**Class:** chain reliability / settlement finality. **Severity:** critical.
**Status:** reproduced end-to-end against the bundled reference SUT.

## The failure

Inclusion in a block is not finality. A chain **reorganization** can drop blocks
that were already mined and replace them, so a transaction that was on-chain a
moment ago disappears. For a payment system that treats the *first* inclusion of
a settlement as final, a reorg produces a **phantom credit**: the system believes
an order is paid, while on-chain the funds have returned to the payer and the
EIP-3009 nonce is free again (so the payer could even spend it elsewhere).

The longer chains with probabilistic finality (and L2s during sequencer
re-orgs / redrives) make this a real operational risk, not a theoretical one.

## How `psv` reproduces it

Anvil makes a reorg deterministic: `take_checkpoint` snapshots the chain before
settlement, and `reorg_to` reverts to it — dropping every block mined since,
exactly like a reorg of that depth. The on-chain test
(`tests/test_r_reorg_invalidation.py`):

1. A payment settles; the SUT books the order as **paid**; the chain shows funds
   moved and the nonce consumed.
2. The settlement is **shallow** — `is_final(current, tx_block, required=5)` is
   `False` (only one confirmation), i.e. reorg-vulnerable.
3. `reorg_to(checkpoint)` drops the settlement block. The independent chain-truth
   oracle now reads the payer's balance restored, the merchant's unchanged, and
   the nonce **free** again — `funds_moved is False`.
4. The SUT's ledger is untouched by the reorg, so it still believes the order is
   paid. The divergence detector raises a critical **`PHANTOM_CREDIT`**.

## Why black-box conformance can't catch this

A protocol tester cannot *cause* a reorg, and the SUT behaves "correctly" by its
own state — it saw a valid settlement and recorded it. Only a harness that can
manipulate the chain (snapshot/revert) and read ground truth independently can
demonstrate that the recorded settlement no longer exists.

## A related anti-pattern: RPC errors as false negatives

While verifying settlement the system queries an RPC node. If it treats an RPC
*error* as "no settlement found", a transient outage turns a real payment into a
silent **unpaid** (a false negative — the silent-loss class). The reference
confirmer instead lets the error propagate (fail loud), and a unit test pins this
behaviour (`test_confirmer_does_not_swallow_rpc_error_into_false_negative`).

## A second timing trap: confirming before inclusion

The mirror image of waiting too long is checking too **early**. A SUT that scans
for the settlement event immediately after submitting — before the tx is mined —
sees nothing and concludes "unpaid", a false negative. With Anvil's automine
paused (`set_automine(False)`), the harness reproduces this deterministically:
submit while mining is paused, observe the premature "unpaid", then mine and show
the payment really went through. The SUT still believes unpaid → the divergence
detector raises a **silent loss** (`tests/test_settlement_delay.py`). The defense
is to wait for inclusion (and ideally `N` confirmations) before deciding.

## Defenses `psv` can verify

- **Finality by confirmations:** treat a settlement as final only at
  `confirmations >= N` (chain-dependent); re-check shallow settlements.
- **Reorg awareness:** watch for dropped blocks and **un-confirm** a settlement
  whose tx is no longer on-chain (the inverse of booking it).
- **Fail loud on RPC errors:** never map an RPC failure to "unpaid"; retry or
  surface a server error instead.
