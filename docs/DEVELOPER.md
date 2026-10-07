# Developer guide

This is the entry point for working on psv. It is a map rather than a manual:
where a topic already has a document, this guide links to it rather than
repeating it. Where the two disagree, the linked document and the code win.

- Plain-language overview (German): [EINFACH-ERKLAERT.md](EINFACH-ERKLAERT.md)
- Review route and safety invariant: [REVIEW-HANDOFF.md](REVIEW-HANDOFF.md)
- Gates, scope boundary, commit rules: [../CONTRIBUTING.md](../CONTRIBUTING.md)

## What psv is

psv is a system-level verification harness for complete x402 payment systems. A
conformance suite asks whether an endpoint speaks the protocol. psv asks a
different question: does the system behind the endpoint believe the same thing
the chain proves? It compares what the system under test (SUT) believes about an
order (paid or unpaid) with a settlement that psv proves itself, from the chain,
and grades the difference. The verdict table is in the [README](../README.md#independent-chain-truth).

psv never trusts the SUT's word, and its CLI never signs or settles anything.
The only signing path is the bundled reference SUT. It is gated by
`psv.safety` and runs only on local Anvil or an allowlisted testnet.

## Module map

[architecture.md](architecture.md) has the data flow, trust boundaries and the
core module table. The full package, as of v0.5.0:

| Module | Role |
|---|---|
| `psv.anvil` | Strict JSON-RPC 2.0 client (`RpcClient`) and local Anvil lifecycle. Contains a broadcast primitive, so it is never imported by read-only consumers. |
| `psv.chain` | Exact ABI encoders, block-pinned token reads, `SettlementTruth`. |
| `psv.payloads` | EIP-3009 / EIP-712 authorization signing for local/test chains. |
| `psv.safety` | Fail-closed pre-signing policy; `ALLOWED_SETTLEMENT_CHAIN_IDS = {1337, 31337, 84532, 11155111}`. |
| `psv.sut` | Strict quote/pay/status adapter contract; `PayResult`, `PayOutcome` (`settled` / `pending` / `unsettled`). |
| `psv.reference_sut` | Calibration SUT (`server.py`, `confirmer.py`) with selectable damage behaviours. |
| `psv.rails` | Attested rail registry (`KNOWN_RAILS`), drift check, live reconciliation. |
| `psv.reconciliation` | Exact settlement identities and ledger-vs-chain diff. |
| `psv.divergence` | The divergence detector: `DivergenceKind`, `Severity`. |
| `psv.differential` | One chain fact against several SUT beliefs at once. |
| `psv.reorg` | Confirmations and finality helpers. |
| `psv.quote_option` | Quote-as-free-option economics (G3). |
| `psv.token_quirks` | Decimals and fee-on-transfer arithmetic. |
| `psv.security_checks` | System-level security checks (C/N classes). |
| `psv.svm_chain` | Offline SVM settlement oracle. |
| `psv.load` | Ramp/spike/soak/breakpoint/recovery load profiles. |
| `psv.report`, `psv.run_record` | Reconciliation report 2.0, run record 1.1, integrity checksum. |
| `psv.pqc` | Opt-in hybrid receipt-v2 verification ([pqc-receipt-v2.md](pqc-receipt-v2.md)). |
| `psv.cli`, `psv.mcp_server` | `psv reconcile` / `psv rail-drift`, and the read-only `psv-mcp` server. |

## Install from a git tag

psv is not on PyPI, and the PyPI name `psv` belongs to an unrelated package, so
always install from a tag:

```bash
pip install "psv @ git+https://github.com/moped2110/psv@v0.5.0"
pip install "psv[chain,mcp] @ git+https://github.com/moped2110/psv@v0.5.0"
```

The extras are listed in the [README](../README.md#install). The core has no
runtime dependencies; `chain` adds web3 and eth-account.

## Development setup

Use Python 3.11 or newer. CI installs a hash-locked environment, and this is the
setup whose green run means the same as CI's
([CONTRIBUTING](../CONTRIBUTING.md#setting-up)):

```bash
python -m venv .venv && . .venv/bin/activate
python -m pip install --require-hashes -r requirements/ci.txt
python -m pip install --no-build-isolation --no-deps -e .
```

`pip install -e ".[dev]"` is faster for iteration, but it resolves freely.

### Local chain (Anvil)

On-chain scenarios need [Foundry](https://getfoundry.sh). CI pins v1.7.1.
[SETUP-onchain.md](SETUP-onchain.md) is the full procedure. In short:

```bash
anvil --chain-id 84532 --silent &
(cd onchain && forge create src/UpgradeableMockUSDC.sol:UpgradeableMockUSDC \
  --rpc-url http://127.0.0.1:8545 \
  --private-key 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80 \
  --broadcast)              # must print 0x5FbDB2315678afecb367f032d93F642f64180aa3
python -m pytest -q -m onchain
```

The key is Anvil's public development key #0, which holds no real value.
`PSV_RPC`, `PSV_CHAIN_ID` and `PSV_TOKEN` override the defaults.

## Tests and gates

CI runs on every branch. Reproduce it in the order given in
[CONTRIBUTING](../CONTRIBUTING.md#the-gates):

```bash
python -m ruff check src tests tools
python -m ruff format --check src tests tools
python -m mypy
python tools/check_function_docs.py
python tools/check_public_repo.py
python tools/validate_support_matrix.py
python -m pytest -q --cov --cov-fail-under=90
python -m pip check
```

- The default `pytest` run is offline. `addopts` excludes the `onchain` and
  `load` markers.
- `python -m pytest -q -m onchain` needs the Anvil setup above.
- `-m load` runs the load profiles and is never part of CI.
- Solidity has its own gates:
  `forge fmt --check --root onchain && forge build --root onchain && forge test --root onchain`.
- `requirements/ci.txt` must match `requirements/ci.in` exactly. Regenerate it
  only with the invocation in `.github/workflows/ci.yml`.
- `check_public_repo.py` rejects personal names and local paths in tracked
  files, because the repository is public.
- Set `set -o pipefail` before shortening output with a pipe.

## How scenarios and verdicts work

**Verdicts.** `psv.divergence` compares a proven `SettlementTruth` with the
SUT's belief and returns one `DivergenceKind`:

- `consistent_paid` and `consistent_unpaid` (severity `ok`);
- `silent_loss`, `phantom_credit`, `underpaid_credit` and
  `over_authorized_settlement` (severity `critical`).

A positive settlement needs exact evidence: one transaction, one log index, the
nonce log and state, and balances at pinned blocks. Aggregate `latest` balance
deltas never prove a payment ([architecture.md](architecture.md#atomic-chain-evidence)).

**Exit codes.** `psv reconcile` returns:

| Exit | Meaning |
|---|---|
| `0` | consistent |
| `1` | critical divergence |
| `2` | invalid input, RPC, evidence or output failure |

`psv rail-drift` returns:

| Exit | Meaning |
|---|---|
| `0` | match |
| `1` | drift or uncalibrated rail |
| `2` | RPC or input failure |

`rail-drift` reads the token at the rail's reviewed block, so it needs an
**archive-capable RPC**. A pruning node rejects the historical read (often HTTP
400 or "missing trie node"); psv then prints the node's message with an archive
hint and exits 2. See [`rails.md`](rails.md#read-only-drift-check).

**Pay outcomes.** Since v0.5.0, `PayResult.outcome` maps a pay answer to
`settled`, `pending` or `unsettled`. `pending` means the answer carries x402's
non-terminal `settlement_pending` *and* names the broadcast transaction. Such an
order is neither paid nor unpaid until that transaction is reconciled.
`settlement_pending` without a hash stays `unsettled`. A `settled` answer that
carries it is rejected.

**Scenarios.** [`support-matrix.json`](../support-matrix.json) is the registry.
Each entry has an `id` (e.g. `PSV-I-002`), `phase`, `severity`, the exact pytest
selector in `test`, an `environment` (`offline`, `anvil`, `load-anvil`,
`mainnet-read-only`, `local-svm`, `not-applicable`) and a `status`
(`implemented`, `passive`, `planned`, `out-of-scope`).
`tools/validate_support_matrix.py` and `tests/test_support_matrix.py` reject
duplicate IDs and selectors that do not resolve. Only `implemented` entries are
certifications. [support-matrix.md](support-matrix.md) says what a green run
does and does not certify.

## Adding things

**A detector or scenario.**

1. Implement it in the module that owns the concern.
2. Test both directions: the divergence it must catch, and the healthy case it
   must not flag ([CONTRIBUTING](../CONTRIBUTING.md#commits-and-pull-requests)).
3. Register a scenario entry in `support-matrix.json` that points at the test,
   with the right environment marker (`@pytest.mark.onchain` for Anvil).
4. Add a scenario note under `docs/` if the damage case needs explaining.
5. Add a `CHANGELOG.md` entry under `[Unreleased]`.

A detector must never take the SUT's assertion as evidence.

**A rail.**

1. Capture the token's identity from the chain, at one finalized block, with
   `tools/capture_rail_attestation.py`. It is read-only and solves the EIP-712
   domain instead of copying `name()`.
2. Add the `RailConfig` to `KNOWN_RAILS` in `psv/rails.py`. `signing_enabled`
   stays `False`.
3. Extend the registry table in [rails.md](rails.md), and the tests in
   `tests/test_rails_unit.py`.

An uncalibrated rail must fail closed (see `jpyc-polygon`).

**A settlement chain.** Adding a chain to `ALLOWED_SETTLEMENT_CHAIN_IDS` is a
reviewed source change. Mainnets are never added, and a runtime override is out
of scope ([CONTRIBUTING](../CONTRIBUTING.md#what-this-project-accepts-and-what-it-does-not)).

## Release process

The pattern of v0.2.0 to v0.5.0:

1. **Feature PRs.**
   - Use Conventional Commits (`feat:`, `fix:`, `docs:`, `build:`, `test:`,
     `chore:`).
   - Every user-visible change gets a line under `## [Unreleased]` in
     `CHANGELOG.md`.
   - CONTRIBUTING asks for signed commits.
2. **Release PR** from a `release/X.Y.Z` branch, with one commit
   `release: X.Y.Z`:
   - bump `version` in `pyproject.toml`;
   - turn `[Unreleased]` into `## [X.Y.Z] — YYYY-MM-DD` above an empty
     `[Unreleased]`.

   `psv.__version__` is read from package metadata, so nothing else changes.
3. **Merge method: rebase** (`gh pr merge --rebase`), after CI is green. Then
   delete the branch. Never force-push `main`.
4. **Annotated tag** `vX.Y.Z` on the release commit:
   - message `psv X.Y.Z`;
   - a paragraph summarizing the release;
   - push it with `git push origin vX.Y.Z`.

   Consumers pin tags, never bare SHAs.
5. **Consumers bump their pins** in their own reviewed PRs:
   - rvf's `onchain` extra;
   - the website backend's `pyproject.toml` / `uv.lock`.

## How the repositories relate

| Repository | Visibility | Current release | Role |
|---|---|---|---|
| x402-conformance | public | v0.7.0 (`05e72a8`) | Black-box x402 protocol client: 81 checks in the default catalog, JSON report 1.4. |
| **psv** | public | **v0.5.0** (`21cee3b`) | System verification against independent chain truth; reconciliation report 2.0, run record 1.1. |
| rvf | private | v0.2.0 (`c1144b6`) | Refund and escrow correctness verifier. |
| X402testwebside (x402 Test Lab) | private | no release tags | Hosted service that runs both engines. |

Upstream x402 has been reviewed through `cb0ec5b` (2026-10-06). The commit is
recorded in x402-conformance's `.github/upstream-reviewed-commit`, and psv's
v0.5.0 settlement notes cite the same commit.

- **x402-conformance and psv share no code.** They agree on formats where they
  meet: x402-conformance's opt-in PQC profile verifies psv's receipt-v2 format
  against a shared canonicalization vector.
- **The website** pins both engines by tag in `backend/pyproject.toml`, and
  `backend/uv.lock` records the resolved commits. It uses psv as a library: the
  analysis modules for its "system" runs, and psv's RPC client and Transfer-log
  decoder for reading chain evidence. For runs it starts the x402-conformance CLI
  as a sandboxed subprocess and reads its versioned JSON report. The check
  catalog and the derived report formats use the engine's public Python APIs.
- **rvf** pins `psv[chain]@v0.5.0` through its optional `onchain` extra.
  - Its import guard (`tests/test_no_originate_guard.py`) forbids `psv.anvil`,
    `psv.payloads` and `psv.reference_sut`, together with every psv module that
    reaches them.
  - It re-derives that list from psv's sources, so a psv release that adds such
    a path fails rvf's CI instead of slipping through.
  - rvf does not depend on x402-conformance.
- **Dependencies point one way.** psv never imports rvf or the website.
  Changing a module's imports here can change what rvf's guard allows, which is
  one more reason a release note should say when a module starts touching
  `psv.anvil`.
