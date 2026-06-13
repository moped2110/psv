# T — Token quirks: decimals & fee-on-transfer (Phase 3)

**Class:** asset semantics. **Severity:** major→critical (underpayment).
**Status:** decimals reproduced offline; fee-on-transfer reproduced on-chain.

Payment systems often hardcode "USDC semantics" (6 decimals, 1:1 transfer). Real
tokens differ, and the assumptions become money bugs.

## Decimals

The same human amount is a different integer at different decimal precisions: at
6 decimals `0.01` is `10_000`; at 18 decimals it is `10_000_000_000_000_000`. A
system that computes atomic amounts with the wrong `decimals` over- or
under-charges by a factor of `10^(d1-d2)`. `psv.token_quirks.to_atomic` makes this
explicit and *fails loudly* on amounts not representable at the given precision,
instead of silently truncating. (Offline: `tests/test_token_quirks_unit.py`.)

## Fee-on-transfer

Some tokens skim a fee on transfer, so the merchant **receives less** than the
amount the payer authorized. A system that confirms settlement on the
authorization amount, or on the value carried by the `Transfer` event, is fooled
— because a deceptive fee token emits a **gross** `Transfer` event while crediting
only the net.

`UpgradeableMockUSDC` models this with `setFeeBps`: the payer is debited `value`,
the merchant nets `value - fee`, and the `Transfer` event still reports the gross
`value`. The reference SUT's event-watching confirmer sees `value >= required` and
reports the order settled — but the merchant's actual balance delta is short. The
harness verifies on the **received delta** (`received_is_sufficient`) and flags
the underpayment. (On-chain: `tests/test_t_fee_on_transfer.py`.)

## Defenses `psv` can verify

- Compute atomic amounts from the token's **actual** `decimals()`, never a constant.
- Confirm settlement on the merchant's **received balance delta** (`>= required`),
  not on the authorization amount or the `Transfer` event value.
- Maintain an allow-list of vetted assets; reject unknown tokens (see the
  fake-token case in [`c-security-gametheory.md`](c-security-gametheory.md)).
