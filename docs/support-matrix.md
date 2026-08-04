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

## Interfaces (what can drive a verification)

Three interfaces reach the same verification core, and none of them widens what it
can do — psv never originates a transaction, so there is no signing path any of them
could reach.

- **CLI (`psv`)** — the full surface. Stdlib only; extras are opt-in per capability.
- **Library** — `psv` is imported as a dependency by the hosted lab and by `rvf`. The
  importing project owns its own guard rails; psv contributes read-only logic.
- **MCP server (`psv-mcp`, `[mcp]` extra)** — `list_rails`, `reconcile_settlement` and
  `rail_drift` for agent callers. Read-only like everything else here.

Two properties of the MCP surface are deliberate and worth stating, because they are
the ones a reader would otherwise have to infer from code:

**The RPC endpoint is not a tool parameter.** It comes from `PSV_RPC_URL`, set by the
operator. Which chain a verdict was proven against is a property of the deployment, not
a per-call choice: an endpoint named by the caller could be a node that lies, and the
tool would also become a general-purpose request primitive aimed at whatever host
appeared in a prompt.

**The endpoint never travels back to the caller.** A hosted provider puts its API key in
the URL path, so RPC errors render the endpoint as scheme and host only, and the MCP
boundary returns a verdict while the detail goes to the operator's log.

## What a green run does not certify

- Production readiness, mainnet safety, legal or regulatory compliance.
- Correct behavior of a customer SUT that was not the target of the run.
- Availability or correctness of third-party RPC providers, facilitators, bridges,
  wallets, or live token deployments outside the evidence block captured for a run.
- Genuinely planned scenarios: additional rails (JPYC/Polygon is registered but
  uncalibrated), the on-chain `local-svm` SVM settlement environment, website integration,
  and operational disclosure workflows.
- Custody, live payouts, or mainnet signing. Those behaviors are intentionally outside
  the product scope.

The offline SVM settlement oracle, the `upto` metered partial-settlement rule, the
multi-asset settlement race, and reorg-aware finality are `implemented` and certified by an
offline green run (see the registry); for SVM, only the live on-chain environment remains
planned.

Only entries with status `implemented` are active certifications. `passive` means
metadata or local domain behavior exists but no live deployment claim is made.
