# predictium_odds changelog

Repos pin this package by commit SHA, never a tag or a branch.

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
