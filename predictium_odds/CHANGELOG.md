# predictium_odds changelog

Repos pin this package by commit SHA, never a tag or a branch.

## 0.3.5 (2026-11-21)

**Bug fix that changes numbers.** `oddsmath.devig_shin` solved Shin's z
with a two-way closed form that was wrong. On lopsided markets z came out
negative and was clamped to 0, so the function returned the PROPORTIONAL
de-vig; on near-even markets it overshot and clamped to 0.2. The favourite-
longshot correction it exists for was never applied on lopsided markets.

| implied (a, b) | 0.3.4 and earlier | proportional | 0.3.5 (true Shin) |
|---|---|---|---|
| 0.70, 0.35 | 0.6667 | 0.6667 | 0.6750 |
| 0.90, 0.15 | 0.8571 | 0.8571 | 0.8750 |
| 0.55, 0.50 | 0.5289 | 0.5238 | 0.5250 |

- z is now the numerical root of sum(q_i) = 1 (`oddsmath.shin_z`, bisection
  on [0, 1)). It reproduces the exact two-outcome identity, q_a = p_a -
  (S - 1) / 2, and agrees with soccer's independent n-way solver.
- `shin_z` raises ValueError for an implied probability outside (0, 1), for
  fewer than two outcomes, and for a book with no overround. `devig_shin`
  still falls back to proportional when there is no overround.
- The existing `test_shin_shrinks_longshot_more_than_proportional` passed
  under the bug by 5.5e-17 of float noise; the new derating test requires a
  real margin.
- Every release from 0.1.0 (`2da418a`) to 0.3.4 carries the bug.

## 0.3.4 (2026-11-17)

Team totals only (`team_total=True`); without the flag nothing changes.

- `parse_contract(..., team_total=True)` also raises `StrikeParseError` on a
  structured row (before, it returned an outcome with `team_code` None) and
  on a ticker that is not under its event (does not start with
  `event_ticker + "-"`).
- `tests/test_nhl.py`: 45 tests (40 before). New fail-closed cases for a rung of
  floor - 0.5, a lowercase subtitle name, a code found only in the middle
  of the matchup ("LA" in "STLAVGK"), a ticker under another event and a
  structured row. The missing-`team_names` test now checks the exact
  ValueError, since `StrikeParseError` is itself a ValueError. Each of
  these guards was removed in turn and a test failed every time.

## 0.3.3 (2026-11-17)

NHL. One behaviour change for existing callers: "wins by" text on a
non-spread contract now raises `StrikeParseError`. Everything else is
additive, and every existing test is unchanged.

- `books.kalshi.NHL_SERIES`: KXNHLGAME, KXNHLSPREAD, KXNHLTOTAL and
  KXNHLTEAMTOTAL, verified live against `/series` and `/markets` (tickers,
  strike types, rules text, fee terms). They go through the existing
  `fetch_game_quotes` and `fetch_ladder`.
- `parse_contract(..., team_total=True, team_names={code: name})` and the
  same two arguments on `fetch_ladder`: a team-total rung ("Utah over 5.5",
  ticker "...-UTA6") carries `team_code` and `subject`. Before, both were
  None, so the two teams' rungs in one event could not be told apart. It
  fails closed and raises `StrikeParseError` unless:
  - the ticker ends in -CODE<rung>, with rung = floor + 0.5;
  - CODE is one of the event ticker's two teams;
  - the subtitle reads "<name> over <line>" with no ":";
  - name == team_names[CODE].
  `team_names` is required with the flag (ValueError, before any request).
  The flag is never inferred, so every existing caller parses as in 0.3.2.
- `parse_contract`: "wins by" text on a non-spread contract raises.
- `books.pinnacle.LEAGUE_IDS["nhl"] = 1456`, not yet verified live (403 from
  the cloud session).
- FanDuel and Bovada NHL need no code: the existing maps read moneyline,
  puck line and total goals. FanDuel lists no per-game NHL team total.
  Bovada is not yet verified live.
- `tests/test_nhl.py`: 40 tests, including one fail-closed test per case
  from the #30 review. Fixtures are shaped on the live rows, with synthetic
  prices.

## 0.3.2 (2026-11-09)

Kalshi reads now retry HTTP 500, 502, 503 and 504 exactly like 429: the same
backoff (2, 4, 8, 16 s, then stop), the same cap, and `Retry-After` honoured.
An unclearable 5xx still raises, so callers still report FAILED (`ok=False`),
never an empty result, and the note reads `HTTP <code> server error, still
failing after 4 retries`. `Retry-After` is honoured only when
0 < value <= 60 s; a negative, `inf`, `nan` or larger value falls back to our
backoff (a negative would make `time.sleep` raise; a huge one would stall
the job). Every other 4xx still fails at once (a
`fetch_market` 404 is not retried). It covers every read through
`_get_page`: market pages, series fees, the batch order book and
`fetch_market`. The NBA props capture brief asked for 1, 2, 4, 8, 16 s; this
keeps the existing 2, 4, 8, 16 s schedule so 429 behaviour is unchanged.

## 0.3.1 (2026-11-09)

Additive only: every existing caller and every existing test is unchanged.
For the internal Kalshi NBA player-prop capture
(nba_prediction_model_2026 `docs/briefs/predictium_odds_kalshi_ladder_fields_2026-11.md`).

- `LadderContract` gains seven optional fields, all `None` when the venue
  omits them, never inferred or zero-filled: `yes_bid_size`, `yes_ask_size`
  (the market object's `*_size_fp`), `no_bid_size`, `no_ask_size` (order book
  only, see below), `last_price` (same `*_dollars` rule as the quotes),
  `market_status` and `result` (verbatim; `result` is `""` until settled).
- `fetch_ladder(..., status=...)` accepts `"open"` (default), `"closed"`,
  `"settled"` or `None` (no filter); anything else raises before a request.
  The filter is not the field: `"settled"` returns markets whose `status`
  reads `"finalized"`.
- `fetch_ladder(..., with_sizes=True)` reads the batch order book, 100
  tickers per request with repeated `tickers=` keys, to fill the NO-side
  sizes by Kalshi's stated equivalence (a YES bid at X is a NO ask at 1 - X,
  same size). A failed size read leaves sizes `None` and says so in the
  note; the capture never fails on it.
- `fetch_market(ticker, *, sport, spread=None)`: one market, same parse, same
  three read states. HTTP 404 is a FAILED read, never an empty one.

## 0.3.0 (2026-09-28)

Kalshi fees from each series' `fee_type` and `fee_multiplier`; settlement
rules verbatim on every contract (PR #9).

## 0.2.0 (2026-09-28)

`fetch_ladder`, fee-inclusive EV, `price_at_strike` (PR #7).
