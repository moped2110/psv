# D3 — Backup/restore vs. chain divergence (silent payment loss)

**Class:** disaster recovery / data integrity. **Severity:** critical.
**Status:** reproduced end-to-end against the bundled reference SUT.

## The failure

A payment system keeps an internal ledger: each settled payment is written as a
paid order. Settlement on a blockchain is **irreversible and external** to that
ledger. So any time the ledger and the chain fall out of sync — a crash and
restore from an older backup, a botched migration, a dropped or rolled-back
write — the system can *forget a payment that actually happened*. The customer
was debited and the merchant credited on-chain, but the system believes the order
was never paid. No error fires; the loss is silent and, without a dedicated job,
permanent.

## How `psv` reproduces it

The reference SUT has an internal ledger (`orders`) with `backup_ledger()` /
`restore_ledger()`, and an optional `reconcile()` job gated by
`reconciliation_enabled`. The test (`tests/test_d3_reconciliation.py`):

1. **Payment A** settles on-chain and is booked. A backup is taken.
2. **Payment B** settles on-chain and is booked *after* the backup.
3. **Crash + restore** to the backup → the ledger loses all memory of B
   (`status(B)` returns `known=false`), while B's funds are on-chain forever.
4. The independent chain-truth oracle still sees B's money move, so the
   divergence detector raises a critical **`SILENT_LOSS`** — the harness catches
   what the system cannot.
5. **Reconciliation** scans on-chain settlements to the merchant and diffs them
   against the ledger's settlement tx hashes. It surfaces exactly the forgotten
   credit (B). With `reconciliation_enabled`, it heals the ledger by booking a
   recovered, paid order; a second pass then finds nothing left to reconcile.

## Why a black-box tester can't catch this

The endpoint, after the restore, behaves "correctly" by its own state: it has no
record of B and returns `402`. Nothing in the protocol exchange is wrong. Only an
oracle that reads the chain *independently of the system's ledger* can see that
the settlement really happened. The reconciliation diff (chain credits minus
ledger records) is the concrete, shippable detector for this entire class.

## Defenses `psv` can verify

- Run a **reconciliation job** continuously: every on-chain credit to the
  merchant must map to a ledger record; alarm on any that don't.
- Treat the unreconciled set as a hard operational alert, not a silent zero.
- Make settlement writes **idempotent and crash-safe** (write-ahead, keyed by the
  settlement tx hash or the EIP-3009 nonce) so a restore cannot drop a confirmed
  payment.
- After any restore/migration, run reconciliation **before** resuming normal
  operation.
