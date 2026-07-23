# Multi-Book Odds Ingestion — Coverage Matrix & Architecture Proposal

_2026-07-23. Deliverables 1–2 of the multi-book odds mission. Probes were run
live from the cloud sandbox (agent-proxy egress); "Mac Mini" notes flag where
Doyle's residential egress would likely behave differently._

## 0. Corrections to the mission premise (verified, important)

1. **There is no Kalshi backend implementation in `nba_prediction_model_2026`**
   — not in any branch or its git history. What exists is a **frontend
   consumer**: `Predictium_Front_End/lib/kalshi.ts` + `/api/kalshi` reading
   `s3://predictium-predictions-prod/kalshi/latest.json`. The publisher of
   that object lives in **no repo in this session** (presumably a script on
   the Mac Mini). The object is live but **stale since 2026-06-14** (NBA
   championship only, `games: {}` — it stopped after the Finals). So Kalshi
   was "generalized" from scratch in the shared layer rather than factored
   out; the NFL win-total client (`nfl_model/data/books/kalshi.py`, shipped
   2026-07-23) is now the org's reference Kalshi implementation.
2. **Kalshi market data requires no credentials.** All market-data endpoints
   (`/trade-api/v2/series|events|markets|orderbook`) are public; keys/RSA
   signing are only for trading, which we never do. The "existing NBA Kalshi
   keys" are therefore not needed for any read path.
3. **`nba_prediction_model_2026` still uses The Odds API** (org-banned):
   `backend/scripts/fetch_the_odds_api*.py`, env `THE_ODDS_API_KEY`, wired
   into `daily_driver.py`. Migrating NBA onto the shared direct-book layer
   removes the last banned-source dependency.
4. **Kalshi API shape change (2026-07):** legacy integer-cent fields
   (`yes_bid`, `yes_ask`, `last_price`, `volume`) now return null; prices are
   in `*_dollars` string fields. Any older Kalshi code (incl. whatever writes
   `kalshi/latest.json`) that reads the integer fields is silently getting
   nulls — worth telling whoever owns the Mac Mini publisher.

## 1. Coverage matrix

Legend: ✅ verified live this session · ◐ works with caveats · ✗ blocked/none
· GL game lines · PP player props · FUT futures/win totals · LIVE in-play.

### Sources already in production

| Source | Access | Auth | GL | PP | FUT | LIVE | Geo/rate | ToS posture | Reliability |
|---|---|---|---|---|---|---|---|---|---|
| **Bovada** | public JSON `bovada.lv/services/sports/event/v2/…` | none | ✅ NFL/MLB/WNBA/CFB/tennis | ✅ | ✅ (NFL team wins verified 32/32 w/ juice; tennis outrights; CFB conf wins listed) | ✗ (we skip `is_live`) | none seen; no key | Gray (undocumented but keyless, no login; 5 repos in prod for months) | High; occasional path renames |
| **FanDuel** | `sbapi.{state}.sportsbook.fanduel.com` + public `_ak` | none | ✅ | ✅ | ◐ **volatile** — win totals left the NFL lobby entirely (the July break; not at any probed customPageId/event/coupon endpoint from this egress) | ✗ | per-state host (NJ everywhere except MLB=MI — should be unified); geo-sensitive | Gray (public web key, undocumented) | Medium — **restructures lobbies silently**; this mission exists because of it |
| **Kalshi** | `api.elections.kalshi.com/trade-api/v2` | **none for market data** | ✅ (`KX{NFL,NBA,WNBA,NCAAF}GAME`, `KXATPMATCH`…) | ◐ (season stat ladders, some game TD/player series) | ✅ (`KXNFLWINS` verified; `KXMLBWINS-*`, `KXNCAAFWINS`, division/champ series) | ✅ (quarters/halves series exist) | documented rate limits; no geo issue | **Cleanest of all: CFTC-regulated, documented public API** | High |

Kalshi series depth by our sports (from the live series dump): NFL 236,
NBA 193, MLB 129, Soccer/UCL/EPL/World Cup 115, Tennis 88, WNBA 80, CFB 56.
Every sport we cover has game-level + futures series. Caveats: thin books on
some contracts (treat like thin books — liquidity floor before quoting), and
**alt-line ladders must be aligned to the consensus main line before any
price comparison** (now codified in `select_aligned_contract`, fail-loud).

### Candidates probed

| Source | Probe result (cloud egress) | GL | PP | FUT | ToS posture | Verdict |
|---|---|---|---|---|---|---|
| **ESPN core API (DraftKings lines)** | ✅ 200 keyless — `sports.core.api.espn.com/v2/...events/{id}/competitions/{id}/odds` returns provider **DraftKings** with open/current spread/total/ML + `propBets` ref | ✅ all US sports | ◐ | ◐ | Unofficial but public, keyless, stable for years, universally used | **Adopt** — gets DK numbers without touching DK's bot walls; also the MLB ROADMAP's requested 3rd source |
| **Pinnacle guest API** | ✅ 200 — `guest.api.arcadia.pinnacle.com/0.1/…` with the public web `x-api-key`; NFL matchups verified | ✅ | ◐ | ✅ | **Gray-to-hostile**: Pinnacle ToS prohibits automated access; US-blocked book (we'd read, not bet) | **FLAG for Doyle** — sharpest consensus anchor in existence; ToS call is a human decision |
| **DraftKings direct** | ✗ 403 (Akamai) | — | — | — | Gray | Skip; DK via ESPN instead. Possible from Mac Mini egress if ever needed |
| **BetMGM** | ✗ 403 (bot wall) | — | — | — | Gray | Skip from cloud; Mac Mini possible, low value vs cost |
| **Caesars** | ✗ 403 (Akamai) | — | — | — | Gray | Skip |
| **bet365** | not probed — known aggressive anti-bot + explicitly ToS-hostile | — | — | — | Hostile | **Do not pursue** |
| **Fanatics / PointsBet** | PointsBet US no longer exists (Fanatics acquisition); Fanatics has no known public JSON | — | — | — | Unknown | Deprioritize |
| The Odds API | — | — | — | — | **ORG-BANNED** | Never |

### Priority recommendation

1. **Kalshi everywhere** (keyless, documented, regulated; every sport covered)
   — as (a) de-vig-free fair-value anchor, (b) distinct sharp signal,
   (c) redundancy when a book breaks. Done for NFL win totals; extend via the
   shared layer.
2. **ESPN/DraftKings adapter** — third traditional-book line for every US
   sport at near-zero ToS risk; replaces NBA's banned Odds API usage as part
   of migrating NBA to the direct-book family.
3. **Book-health monitoring** before any further adapters (deliverable 5) —
   more sources without drift detection just multiplies silent-failure
   surface.
4. **Pinnacle** — only after Doyle's explicit ToS decision.
5. BetMGM/Caesars/direct-DK — only if a Mac-Mini-egress runner is ever
   justified; not worth it while 1–3 are unshipped.

## 2. Normalized schema + adapter interface (proposal)

One shape every source emits ("quote = one priced side of one market"):

```
Quote {
  sport:        "nfl" | "nba" | "mlb" | "wnba" | "cfb" | "tennis" | "worldcup"
  event_key:    canonical event id (sport-local: "BUF@KC_2026-09-13", match pid pair, …)
  book:         "bovada" | "fanduel" | "kalshi" | "espn_dk" | …
  is_exchange:  bool (Kalshi: prices are probabilities, no vig)
  market:       "moneyline" | "spread" | "total" | "win_total" | "outright" | prop key
  side:         "home"|"away"|"over"|"under"|"a"|"b"|runner name
  line:         float | null (betting convention: negative = home favored)
  price_american: int   (exchange quotes converted at the boundary)
  implied_prob: float   (raw, vig-inclusive; de-vig is a downstream utility)
  ts:           fetch timestamp (UTC ISO)
  source_ids:   {event_id/link/ticker…} for traceability
  extras:       {yes_bid, yes_ask, liquidity, …} (exchange-only fields)
}
```

Adapter interface (each book implements identically):
`fetch_game_lines(sport) -> [Quote]`, `fetch_props(sport, event) -> [Quote]`,
`fetch_futures(sport, market) -> [Quote]`, plus
`expected_coverage(sport) -> CoverageSpec` (feeds the health monitor: which
markets/how many events this book is supposed to list in-season).

Shared utilities (single implementations, replacing ~9 divergent copies):
- odds math: `american_to_decimal/implied`, `prob_to_american`
- de-vig: multiplicative 2-way, proportional, **Shin** (WNBA's, canonical for
  ML), **power** (tennis's, canonical for n-way futures)
- `best_line(quotes)` (line-shopping), `consensus(quotes)` (decimal-space
  mean price + median line), and **`align_main_line(ladder, consensus_line)`
  — the consensus-main-line selection rule as a first-class utility** (ladder
  of alt lines or Kalshi strikes → the one entry equivalent to the consensus
  main line; returns nothing + loud signal when none is within tolerance;
  never nearest-strike)
- health: per-source `SourceReport {book, sport, market, n_markets, ok,
  expected_min}` emitted every fetch, evaluated against `CoverageSpec`

## 3. Architecture decision — NEEDS DOYLE'S CALL

**Option A — shared `predictium-odds` package (new repo, pip-installable via
git).** Book clients + schema + utils + health harness live once; each model
repo pins a ref and keeps a thin sport config (team alias resolver, page ids,
market list). Pros: kills the 9-way de-vig duplication and 4-way copy-pasted
FanDuel constants; one fix propagates; health monitoring is uniform. Cons: a
new deploy dependency for 6 independently-deployed repos (GHA + Mac Mini
launchd both must `pip install git+…@tag`); cross-repo change coordination.

**Option B — per-repo modules, shared by convention (status quo + porting).**
Pros: zero deploy coupling. Cons: the duplication that caused today's
divergence (MLB on `.mi` host, CFB dropping per-book tags, NBA never migrating
off the banned aggregator) keeps compounding; book-health monitoring gets
re-implemented 6 times or not at all.

**Recommendation: Option A**, rolled out behind flags with current per-repo
code kept as fallback (deliverable 6), starting with the two repos that gain
most (NBA — banned-source removal; NFL — already has the reference Kalshi
client to donate). Tagged releases, never floating `main`, so a bad package
change can't break six pipelines at once.

## 4. Status

- ✅ First concrete win shipped: NFL season win-total feed is live again
  (Bovada board + Kalshi KXNFLWINS with strict main-line alignment; FanDuel
  parse kept for auto-recovery; per-source health line + loud warnings).
  `nfl_prediction_model_2026` branch `claude/predictium-multi-book-odds-zdirww`.
- ✅ **Architecture approved by Doyle 2026-07-23**: shared package. Pinnacle
  approved (internal-only; see `predictium_odds/books/pinnacle.py` for the
  honest ToS framing and the `redistributable=False` containment). Priority
  order confirmed: Kalshi everywhere → ESPN/DK (+ NBA off the banned
  aggregator) → health monitoring → rollout.
- ✅ **`predictium_odds/` package shipped** (this repo, pip-installable
  subdirectory — the seed of the potential standalone odds-service product):
  schema, oddsmath (Shin/power ports), lines (best/consensus/align),
  health, and five adapters (bovada, fanduel, kalshi, espn_dk, pinnacle),
  all verified live 2026-07-23; 20 fixture tests. See its README for the
  per-repo rollout pattern (flag + fallback + parity check).
- ⏭ Next: wire NFL repo's win-total path onto the shared layer (its
  in-repo implementation is the donor), then NBA migration (removes The
  Odds API), then per-repo rollout with `CoverageSpec`s and CI alerting.
- ⚠ Revenue-service note: redistributing scraped book prices commercially
  is a materially different ToS/legal posture than internal modeling use —
  needs a real legal read before any productization. Kalshi (regulated,
  documented) and de-vigged *derived* consensus values are the defensible
  core of such a product; raw Bovada/FanDuel/Pinnacle passthrough is not.
