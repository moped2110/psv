# G3 — Quote as a free option

**Class:** economic / market design. **Severity:** critical (systematic loss).
**Status:** reproduced — as an offline economic simulation and on-chain against
the bundled reference SUT.

## The failure

To pay with x402 a buyer first gets a **quote**: a price locked for some validity
window. If the priced resource has a fair value that moves (an FX rate, a
commodity, a gas-priced compute job, anything market-linked) and the system
honors the quote later **without re-checking the price**, the quote is a free
call option:

- Request many quotes (cheap, often free).
- Execute only the ones that became favorable — fair value rose above the locked
  price, so the buyer pays less than the resource is now worth.
- Abandon the rest at no cost.

Because the buyer only ever exercises in-the-money, the system's expected outcome
is a **systematic loss**. There is no "attack" to detect — it is the rational use
of an option the system gave away. The longer the quote's validity and the wider
the price swings, the larger the bleed.

## How `psv` models it

`psv.quote_option` is the pure economics:

- `option_value(locked, fair_now) = max(0, fair_now − locked)` — the buyer's gain
  (the system's loss) if exercised; zero out of the money (buyer walks away).
- `quote_is_stale(locked, fair_now, tolerance)` — whether honoring the quote
  underprices the resource beyond a tolerance. A re-pricing guard rejects exactly
  these.
- `simulate_attacker(rounds, reprice, tolerance)` — a rational buyer over many
  rounds; quantifies the loss a vulnerable system bleeds versus a guarded one
  (which is driven to zero).

The reference SUT carries a movable `fair_price` oracle, a quote validity window
(`quote_ttl`) and an optional `reprice_on_pay` guard. The on-chain test
(`tests/test_g3_quote_option.py`):

- **Vulnerable SUT** (`reprice_on_pay=False`): quote locks price *P*; the fair
  value then triples; the buyer pays the stale quote and the SUT **settles it
  on-chain**, underpaid by the full option value.
- **Guarded SUT** (`reprice_on_pay=True`): same setup, but the SUT **rejects the
  stale quote before any settlement** — no tx submitted, no nonce burned, no funds
  moved.

## Defenses `psv` can verify

- **Re-price at execution**: reject or re-quote if the locked price deviates from
  current fair value beyond a tight tolerance (the guard above).
- **Short quote validity**: make the option's time value negligible.
- **Charge for optionality**: a small non-refundable quote fee or a deposit
  removes the free lunch.
- **Hedge or cap** exposure per quote and in aggregate for market-linked pricing.
