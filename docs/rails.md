# Rails — multi-asset reconciliation (USDC / JPYC / EURC)

The harness's chain-truth oracle (`psv.chain.TokenView`) and divergence detector
are rail-agnostic: `balanceOf`, `authorizationState` and the `AuthorizationUsed`
log are standard across every EIP-3009 token. `psv.rails` pins the constants for
the rails we care about so the *same* harness reconciles any of them.

## Known rails (`psv.rails.KNOWN_RAILS`)

| key | asset | chain | decimals | EIP-712 domain |
|---|---|---|---|---|
| `mock-anvil` | Local MockUSDC | Anvil 84532 | 6 | `USDC` / `2` |
| `usdc-base` | USDC | Base 8453 | 6 | `USD Coin` / `2` |
| `jpyc-polygon` | JPYC | Polygon 137 | 18 | `JPY Coin` / `1` |
| `eurc-base` | **EURC** | Base 8453 | 6 | _unset — verify before signing_ |

**EURC is the MiCA-era EUR rail.** Circle's EURC implements EIP-3009
`transferWithAuthorization` natively and is served by the CDP facilitator, so it
rides the same `exact/eip3009` path as USDC — no special facilitator. The
6-vs-18 decimals split (USDC/EURC = 6, JPYC = 18) is itself the decimals damage
case (`psv.token_quirks`).

## Read-only — the money invariant holds

`reconcile_live(token, payer=…, payee=…, nonce=…, payer_before=…, payee_before=…,
sut_believes_paid=…)` **only reads** the chain (balances + whether the EIP-3009
nonce was consumed) and compares it to what the system believes, returning a
`Divergence` (`consistent_paid` / `phantom_credit` / `silent_loss` /
`underpaid_credit`). It **never
signs or settles** on a real rail — outbound value stays testnet/Anvil only. A
`RailConfig`'s EIP-712 domain (`token_name`/`token_version`) is therefore needed
only for the local Anvil *signing* path; live read-only reconciliation ignores it,
which is why `eurc-base` ships with the domain unset (verify on-chain before any
signing use).

```python
from psv.anvil import RpcClient
from psv.rails import get_rail, token_for_rail, reconcile_live

rail = get_rail("eurc-base")
token = token_for_rail(rail, RpcClient(endpoint="https://mainnet.base.org"))
# snapshot balances before the payment, then after settlement reconcile read-only:
d = reconcile_live(token, payer=payer, payee=merchant, nonce=nonce,
                   payer_before=p0, payee_before=m0, sut_believes_paid=claimed)
if d.is_failure:
    print(d.message)   # phantom_credit / silent_loss
```

Offline-tested in `tests/test_rails_unit.py` (registry + all three divergence
outcomes over a fake JSON-RPC transport). A live Polygon/Base reconciliation run is
the next step (P-11 Stage 2), using a per-chain `safe_window_seconds` for finality
(Polygon ≈ 120s, checkpoint-based) rather than a fixed confirmation count.
