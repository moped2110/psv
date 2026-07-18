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
| `jpyc-polygon` | Polygon 137 | JPYC | 18 | `finalized` | uncalibrated; fails closed |

The Base attestations were reviewed on 2026-07-18 against finalized block
`48,783,151`. They pin the token address, proxy runtime hash, implementation
slot, implementation address, implementation runtime hash, expected decimals,
interface, and authoritative sources. The JPYC address and metadata remain in
the registry for planned calibration, but live reconciliation is rejected until
the equivalent code/proxy identity is pinned.

Authoritative metadata sources:

- [Circle USDC contract addresses](https://developers.circle.com/stablecoins/usdc-contract-addresses)
- [Circle EURC contract addresses](https://developers.circle.com/stablecoins/eurc-contract-addresses)
- [JPYC contract notice](https://corporate.jpyc.co.jp/news/posts/Notice)
- [EIP-3009](https://eips.ethereum.org/EIPS/eip-3009)

## Read-only drift check

The drift check validates the RPC chain, reviewed block anchor, safe/finalized
block, token and implementation bytecode, proxy implementation, and callable
read interface. It returns exit 0 on a match, 1 on drift or an uncalibrated rail,
and 2 on an RPC or input failure.

```bash
psv rail-drift --rail usdc-base --rpc-url https://mainnet.base.org
psv rail-drift --rail eurc-base --rpc-url https://mainnet.base.org
```

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
