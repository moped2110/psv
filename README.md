# psv — payment-system verification harness

`psv` is a system-level test harness for complete x402 payment systems. A
black-box conformance suite checks whether an endpoint speaks the protocol; psv
checks whether the system behind it settles, books and recovers correctly under
chain, RPC, reorg and load failures.

## Independent chain truth

A payment system holds a belief about an order: paid or unpaid. psv independently
proves one settlement from the chain. A verdict is bound to one chain, canonical
block, transaction receipt, exact Transfer log, EIP-3009 nonce log/state, token,
payer, payee and amount. Parent/settlement balances are read at pinned block
numbers, and the block identity is rechecked before a verdict is emitted.

| Proven chain result | SUT belief | Verdict |
|---|---|---|
| exact amount received | paid | consistent paid |
| no settlement | unpaid | consistent unpaid |
| funds received | unpaid | **silent loss** — customer paid and gets nothing |
| no settlement | paid | **phantom credit** — resource released for free |
| too little received | paid | **underpaid credit** — partial/net payment accepted as full |

The three divergence cases are system failures that protocol conformance alone
cannot detect.

## Components

- `psv.anvil`: strict JSON-RPC client plus deterministic Anvil process control.
- `psv.chain`: pinned token reads, exact ABI encoders and settlement evidence.
- `psv.sut`: strict quote/pay/status adapter with bounded HTTP input and lifecycle.
- `psv.reference_sut`: a miniature system used to calibrate the harness end to end.
- `psv.safety`: fail-closed pre-signing checks for chain, token code, payer, payee
  and exact quoted amount. Only explicit local/test chains are allowed.
- `psv.rails`: versioned rail metadata, proxy/code identity and finality rules.
  Live reconciliation is read-only; uncalibrated or drifted rails fail closed.
- `psv.reconciliation` and `psv.divergence`: exact settlement identity, ledger
  reconciliation and graded verdicts.
- `psv.report` and `psv.run_record`: versioned schemas, stable reason codes,
  evidence provenance, privacy policy and collision-safe integrity records.
- `psv.load`: ramp/spike/soak/breakpoint/recovery stages, facilitator pooling,
  attempted/successful throughput and bounded error evidence.

The demonstrated damage scenarios include event/ABI drift (SC1), ledger restore
divergence (D3), quote-as-option (G3), reorg invalidation, idempotency, delayed or
stuck settlement, facilitator crash, fee-on-transfer underpayment, cross-chain
replay, fake/EOA assets and differential SUT behavior. See [`docs/`](docs/) for
the scenario explanations.

The machine-readable source of support truth is
[`support-matrix.json`](support-matrix.json). The exact guarantees and limitations
of a green run are in [`docs/support-matrix.md`](docs/support-matrix.md).

## Install

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Available extras are `chain`, `sut`, `dev` and `release`. The `sut` extra includes
its signing/recovery runtime dependency; the core CLI remains dependency-light.

## Test

```bash
# Offline default gate
pytest -q
mypy
ruff check src tests tools
ruff format --check src tests tools
python tools/check_function_docs.py
python tools/check_public_repo.py
python tools/validate_support_matrix.py

# Opt-in local-chain and load gates
pytest -q -m onchain
pytest -q -m load
```

On-chain tests require Anvil and the deterministic mock-token deployment described
in [`docs/SETUP-onchain.md`](docs/SETUP-onchain.md). Solidity has its own gates:

```bash
forge fmt --check --root onchain
forge build --root onchain
forge test --root onchain
```

## Read-only reconciliation CLI

The CLI requires an exact settlement identity and invoice amount. It never falls
back to unattributed aggregate balance deltas.

```bash
psv reconcile \
  --rail mock-anvil \
  --payer 0x1111111111111111111111111111111111111111 \
  --payee 0x2222222222222222222222222222222222222222 \
  --nonce 0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --tx-hash 0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
  --log-index 0 \
  --required-amount 10000 \
  --payer-before 1000000 \
  --payee-before 0 \
  --sut-paid
```

Exit codes are stable: `0` consistent, `1` critical divergence, `2` invalid
input/RPC/evidence/output failure. JSON/Markdown outputs and the default run
record contain the evidence needed to reproduce the decision. A run-record hash
is an integrity checksum, not a signer-authenticity proof.

Rail drift can be checked without moving value:

```bash
psv rail-drift --rail usdc-base --rpc-url https://mainnet.base.org
```

## Safety

No mainnet money, ever. Reconciliation and drift checks are read-only. The only
bundled signing path rejects EVM mainnets and unknown chains before transaction
construction/signing and permits only local Anvil or explicitly reviewed testnet
chains. Tests use Anvil's public development keys and local test funds. psv is a
verification tool, not custody, a payment service, or legal/financial advice.

## License

Apache-2.0.
