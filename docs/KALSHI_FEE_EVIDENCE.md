# Kalshi fees: what is known, and from where

_2026-09-28, Books & Odds session. Written for golf spec 0038 §6 and §9
(items 2 and 3) and for every sport that prices Kalshi EV through
`predictium_odds.oddsmath.kalshi_ev_at_ask`._

## The rule the shared helper follows

The fee comes from the SERIES, never from a constant. The caller reads
`fee_type` and `fee_multiplier` from `/series/{ticker}` at capture time
(`books.kalshi.fetch_series_fees`, and `fetch_ladder` attaches them to every
contract). The helper applies

    fee = fee_multiplier x 0.07 x contracts x ask x (1 - ask), ceil to $0.000001

for the three quadratic fee types and returns **None** (no fee, therefore no
EV claim) for `flat`, for any fee type not listed, and for a missing,
non-numeric, non-finite or negative multiplier. There is no default fee type
and no default multiplier.

## Sources read, and what each one says

Retrieved 2026-09-28 03:11 UTC, one GET each, plain `curl`, no retries.
Sizes and sha256 are of the response bodies as received.

| Source | HTTP | Bytes | sha256 (first 16) |
|---|---|---|---|
| `https://docs.kalshi.com/api-reference/market/get-series` | 200 | 461,682 | `399ab626c90231a9` |
| `https://docs.kalshi.com/getting_started/fee_rounding` | 200 | 263,525 | `9cd3b54bc3c6159a` |
| `https://kalshi.com/docs/kalshi-fee-schedule.pdf` | 429 | 33,946 | `ecf67990068fdf60` |

**The fee schedule PDF has still been read by nobody.** Its 429 body is a
"Vercel Security Checkpoint" page, a bot check rather than rate limiting, so
retrying is pointless. We do not try to get past a bot check. This is
golf's `research/tos/kalshi_terms_and_fees.txt` result again, now with its
cause identified.

### 1. API reference, get-series (verbatim)

`fee_type`:

> "FeeType is a string representing the series' fee structure. Fee
> structures can be found at https://kalshi.com/docs/kalshi-fee-schedule.pdf.
> 'quadratic' is described by the General Trading Fees Table,
> 'quadratic_with_maker_fees' is described by the General Trading Fees Table
> with maker fees described in the Maker Fees section,
> 'quadratic_with_combo_maker_fees' is the same maker-fee structure with a 0.5
> maker multiplier instead of 0.25, 'flat' is described by the Specific
> Trading Fees Table."

enum: `quadratic`, `quadratic_with_maker_fees`,
`quadratic_with_combo_maker_fees`, `flat`.

`fee_multiplier`:

> "FeeMultiplier is a floating point multiplier applied to the fee
> calculations."

What follows from that: the three quadratic types share the **General
Trading Fees Table** for takers and differ only in what makers pay. We buy at
the ask, so we are takers, and all three price the same way for us. `flat`
uses a different table we have not read, so it prices as None.

### 2. Fee rounding page (verbatim)

> "Compute trade_fee = ceil_6dp(model_fee)", then the balance change is
> aligned to the member's precision ("Non-direct member balances are aligned
> to $0.01"), and "The fee accumulator applies across all fills of an order
> so that the total fee converges to what a single equivalent fill would
> cost."

Worked example: "a buy has -$0.055000 of signed revenue and a model fee of
$0.00363825: trade fee = ceil_6dp($0.00363825) = $0.003639 ... The balance
changes by -$0.06; the trade fee plus rounding fee is exactly $0.005."

### 3. Where 0.07 comes from: corroborated, not quoted

No document we have read states the 0.07 base. It lives in the General
Trading Fees Table, which is in the unread PDF. It is corroborated by
Kalshi's own worked example above: a one-contract buy at $0.055 with
multiplier 1 gives

    0.07 x 1 x 0.055 x (1 - 0.055) = 0.00363825

which is the example's model fee to the last digit. The helper reproduces the
whole example, including the $0.06 balance change
(`tests/test_ladders.py::test_kalshi_worked_example_reproduces_to_the_cent`).
Treat 0.07 as strongly corroborated. If the PDF is ever read and disagrees,
`KALSHI_GENERAL_FEE_COEFF` is the one constant to change.

## fee_type values observed live

Every series on Kalshi, 2026-09-28 (`GET /series?limit=1000`, one page, no
cursor returned, 14,399 series):

| fee_type | fee_multiplier | series |
|---|---|---|
| `quadratic` | 1 | 14,204 |
| `quadratic_with_maker_fees` | 1 | 159 |
| `quadratic` | 0.5 | 18 |
| `quadratic` | 0 | 14 |
| `quadratic_with_combo_maker_fees` | 1 | 3 |
| `quadratic_with_maker_fees` | 0.5 | 1 |

**No series uses `flat` today.** The 0.5 multipliers are all MLB game-day
series (spread, total, strikeouts, outs, hits, total bases, F5 and the rest;
`KXMLBGAME` is `quadratic_with_maker_fees` at 0.5). The zeros are 14
non-sports series (politics, crypto, economics). The combo type is on three
"MVE" multi-game exotics only.

The series the fleet reads:

| Series | fee_type | multiplier |
|---|---|---|
| KXNFLGAME, KXNFLSPREAD, KXNFLTOTAL | quadratic_with_maker_fees | 1 |
| KXNFLWINS | quadratic | 1 |
| KXNCAAFGAME, KXNCAAFSPREAD, KXNCAAFTOTAL | quadratic_with_maker_fees | 1 |
| KXMLBGAME | quadratic_with_maker_fees | 0.5 |
| KXMLBSPREAD, KXMLBTOTAL, KXMLBKS, KXMLBOUTS | quadratic | 0.5 |
| KXWNBAGAME | quadratic_with_maker_fees | 1 |
| KXWNBASPREAD, KXWNBATOTAL | quadratic | 1 |
| KXNBAGAME, KXNBASPREAD, KXNBATOTAL | quadratic_with_maker_fees | 1 |
| KXATPMATCH, KXWTAMATCH | quadratic_with_maker_fees | 1 |
| KXEPLGAME, KXUCLGAME, KXLALIGAGAME | quadratic_with_maker_fees | 1 |
| KXEPLSPREAD, KXEPLTOTAL | quadratic | 1 |
| KXPGATOUR | quadratic_with_maker_fees | 1 |
| KXPGATOP5/10/20, KXPGAMAKECUT, KXPGAH2H | quadratic | 1 |

Rule of thumb, NOT a rule to code against: headline winner series carry
maker fees, and ladders and props do not. Read the series every time.

What the repos had recorded on `main` before this survey: golf's captures
and fixtures carry `quadratic` (outrights) and `quadratic_with_maker_fees`,
multiplier 1. Tennis's work order (`docs/kalshi_fee_ev_work_order.md`)
records `quadratic` for outrights and `quadratic_with_maker_fees` for match
series, multiplier 1. No NFL, CFB,
WNBA, soccer or MLB code on `main` records either field.

## Found in golf while answering this: a fee about 14 times too high

`golf_prediction_model_2026/golf_model/paper/select.py::kalshi_fee_per_contract`
computes `ceil_to_cent(fee_multiplier * price * (1 - price))`, with **no
0.07**. `scripts/paper_ledger_run.py:158` feeds it the captured row's
`fee_multiplier`, which is 1 on every golf series. At a 50c ask it charges
$0.25 a contract where Kalshi charges $0.0175, about 14 times too much, so
every Kalshi row in the paper ledger is priced far worse than it was.
The tests pass `fee_multiplier=0.02`, which keeps the number looking
plausible and hides the gap.

The same line maps a missing multiplier to `0.0` (`r.get("fee_multiplier")
or 0.0`), which prices an unknown fee as free. That is the opposite failure,
and it breaks the rule above.

Spec 0038 §6 already plans to replace this function with the shared helper.
Until then, golf's paper-ledger Kalshi EVs and anything scored on them are
wrong. Raised to Portfolio/Risk and golf 2026-09-28; the fix is golf's.
