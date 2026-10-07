# Attested payment rails

`psv.rails` reconciles one exact EIP-3009 settlement against read-only chain
evidence. It never signs or submits a transaction on a public rail. Every
`RailConfig` therefore has `signing_enabled=False`, including rails whose domain
metadata is known.

## Registry

| Key | Network | Token | Decimals | Finality tag | Runtime status |
|---|---:|---|---:|---|---|
| `mock-anvil` | local 84532 | MockUSDC | 6 | `latest` | calibrated test fixture |
| `usdc-base` | Base 8453 | USDC | 6 | `finalized` | calibrated read-only |
| `eurc-base` | Base 8453 | EURC | 6 | `finalized` | calibrated read-only |
| `usdc-polygon` | Polygon 137 | USDC | 6 | `finalized` | calibrated read-only |
| `usdc-celo` | Celo 42220 | USDC | 6 | `finalized` | calibrated read-only |
| `usdc-celo-sepolia` | Celo Sepolia 11142220 | USDC | 6 | `finalized` | calibrated read-only |
| `usdc-base-sepolia` | Base Sepolia 84532 | USDC | 6 | `finalized` | calibrated read-only |
| `usdt0-flare` | Flare 14 | USD₮0 | 6 | `finalized` | calibrated read-only |
| `jpyc-polygon` | Polygon 137 | JPYC | 18 | `finalized` | uncalibrated; fails closed |

A calibrated attestation pins the token address, runtime code hash, proxy
implementation slot, implementation address and its runtime hash, expected
decimals, interface, EIP-712 domain, and authoritative sources — all captured
from **one** finalized block, because values from different blocks describe a
state that never existed together. Each rail carries its own review date: the
Base pair was reviewed 2026-07-18 at block `48,783,151`, the four newer rails on
2026-08-13 at their own blocks, and `usdc-base-sepolia` on 2026-10-07 at block
`47,801,036`.

The EIP-712 domain is **solved, not copied**. `tools/capture_rail_attestation.py`
reads the contract's `DOMAIN_SEPARATOR()` and reports which name/version pair
reproduces it, because `FiatTokenV2_2` does not necessarily derive its domain
from the current `name()`. That distinction is not academic: `usdt0-flare`
exposes no `version()` at all, so its attested `"1"` could only be recovered by
solving for it. A domain copied wrong is a claim no test would catch, since every
test would share the assumption.

`jpyc-polygon` remains registered and uncalibrated on purpose. It is the control
case: live reconciliation is rejected until the same evidence is captured, and a
test asserts it stays that way — if it ever flips without a capture, calibration
has become a label.

Authoritative metadata sources:

- [Circle USDC contract addresses](https://developers.circle.com/stablecoins/usdc-contract-addresses)
- [Circle EURC contract addresses](https://developers.circle.com/stablecoins/eurc-contract-addresses)
- [JPYC contract notice](https://corporate.jpyc.co.jp/news/posts/Notice)
- [x402 default assets](https://github.com/x402-foundation/x402/blob/main/DEFAULT_ASSETS.md) — authoritative for *which* token an x402 endpoint on a chain quotes, never for what the contract is
- [EIP-3009](https://eips.ethereum.org/EIPS/eip-3009)
- [x402 SDK default assets @ cb0ec5bc](https://github.com/x402-foundation/x402/blob/cb0ec5bc/python/x402/mechanisms/evm/default_assets.py) — `eip155:84532` → `0x036CbD53842c5426634e7929541eC2318f3dCF7e`, domain `USDC`/`2`, 6 decimals (the Go table at the same commit agrees)

### `usdc-base-sepolia` review (2026-10-07)

| Field | Value | Evidence |
|---|---|---|
| Token | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` | Circle's USDC address table lists it for Base Sepolia; upstream x402's default-asset tables (Python and Go, `cb0ec5bc`) use it for `eip155:84532` |
| Reviewed block | `47,801,036`, hash `0x70b0837b…a70266` | finalized block at capture time |
| Proxy code SHA-256 | `e878b99f…996d28` | `eth_getCode` at the reviewed block |
| Implementation | `0xd74cc5d436923b8ba2c179b4bca2841d8a52c5b5` (Circle proxy slot) | `eth_getStorageAt` at the reviewed block |
| Implementation code SHA-256 | `9e410c49…010eba77` | `eth_getCode` at the reviewed block |
| EIP-712 domain | `USDC` / `2` | solved from `DOMAIN_SEPARATOR()` = `0x71f17a3b…4c9818`, matches `name()`/`version()` |
| Decimals, EIP-3009 state | `6`; `authorizationState` readable | `eth_call` |

Captured with `tools/capture_rail_attestation.py --rpc https://sepolia.base.org`
and re-captured from `https://base-sepolia-rpc.publicnode.com`; every field was
identical at the same finalized block. `psv rail-drift --rail usdc-base-sepolia`
matches on both endpoints. The rail shares chain id 84532 with the local
`mock-anvil` fixture but not its token; the rail key selects the rail.

## Read-only drift check

The drift check validates the RPC chain, reviewed block anchor, safe/finalized
block, token and implementation bytecode, proxy implementation, and callable
read interface. It returns exit 0 on a match, 1 on drift or an uncalibrated rail,
and 2 on an RPC or input failure.

```bash
psv rail-drift --rail usdc-base --rpc-url https://mainnet.base.org
psv rail-drift --rail eurc-base --rpc-url https://mainnet.base.org
```

```bash
psv rail-drift --rail usdc-base-sepolia --rpc-url https://sepolia.base.org
```

**The drift check needs an archive-capable RPC.** It re-reads the token at the
reviewed block, which for most rails is weeks or months old. Pruning nodes refuse
that: `forno.celo-sepolia.celo-testnet.org`, for example, answers HTTP 400 with
`block is more than 10064 blocks behind head`. Since 2026-10, psv shows the node's
own message and an archive hint instead of a bare `HTTP Error 400`. Use an
archive endpoint (e.g. `https://rpc.ankr.com/celo_sepolia` for Celo Sepolia, which
matched on 2026-10-07) or your own archive node. A pruned node fails closed
(exit 2), never with a false match.

CI runs both Base observations on a schedule. The job is deliberately absent
from pull-request gates so external RPC availability cannot make offline changes
flaky. JPYC is not scheduled while its attestation is incomplete.

## Exact reconciliation

`psv reconcile` binds the verdict to one transaction, one log index, one
authorization nonce, one token, and one payer/payee pair. It verifies the chain
ID and runtime attestation, reads a canonical receipt, checks both Transfer and
AuthorizationUsed evidence, pins parent/settlement/finality blocks, rejects
same-block transfer races and removed logs, then compares the exact received
amount with the invoice amount and the SUT belief.

```bash
psv reconcile \
  --rail mock-anvil \
  --payer 0x1111111111111111111111111111111111111111 \
  --payee 0x2222222222222222222222222222222222222222 \
  --nonce 0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --tx-hash 0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
  --log-index 0 \
  --required-amount 1000000 \
  --payer-before 5000000 \
  --payee-before 0 \
  --sut-paid \
  --rpc-url http://127.0.0.1:8545
```

Reports use contract version 2.0 and include the full evidence needed to
reproduce a verdict. Run records use schema version 1.1 and an integrity
checksum; the checksum detects corruption or editing but is not a signature or
an external trust anchor.

## Limits

A green result proves only the registered scenario and evidence contract. It
does not certify an entire payment system, legal compliance, or economic safety.
See [support-matrix.md](support-matrix.md) for the enforced scenario registry.
