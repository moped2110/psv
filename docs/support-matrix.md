# Verification support matrix

`support-matrix.json` is the authoritative, machine-validated inventory of shipped,
passive, planned, and explicitly out-of-scope scenarios. Each implemented scenario
names the exact pytest function and required environment. CI rejects duplicate IDs
and renamed or missing registered tests.

## What a green run certifies

An offline green run certifies deterministic parsers, encoders, divergence rules,
reconciliation logic, RPC failure handling, schemas, and the registry itself for the
tested inputs. It does not exercise a blockchain.

An Anvil green run additionally certifies the registered EVM system scenarios against
the bundled mock token and reference SUT on the configured local chain: settlement,
idempotency, cross-chain replay rejection, event drift, reorg invalidation, delayed or
stuck settlement, fee-on-transfer underpayment, recovery, and reconciliation. Load
scenarios remain opt-in under the `load` marker and include concurrent ramp, spike,
soak, breakpoint, and recovery profiles over independent facilitator accounts.

The scheduled read-only rail job observes the pinned USDC/Base and EURC/Base runtime
and proxy identities. It never signs or submits a transaction, and it is intentionally
separate from pull-request gates. JPYC/Polygon remains registered but uncalibrated and
fails closed.

## What a green run does not certify

- Production readiness, mainnet safety, legal or regulatory compliance.
- Correct behavior of a customer SUT that was not the target of the run.
- Availability or correctness of third-party RPC providers, facilitators, bridges,
  wallets, or live token deployments outside the evidence block captured for a run.
- Planned scenarios, including SVM, additional rails, partial-settlement semantics,
  multi-asset races, website integration, and operational disclosure workflows.
- Custody, live payouts, or mainnet signing. Those behaviors are intentionally outside
  the product scope.

Only entries with status `implemented` are active certifications. `passive` means
metadata or local domain behavior exists but no live deployment claim is made.
