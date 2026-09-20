# Venue market inventory — Novig, Kalshi, Polymarket US

_Built 2026-09-20 by the Books & Odds session, from live probes, so the seven
sport sessions do not each rediscover the same fact set. Counts move; the
naming conventions and access patterns are the durable part. Re-probe counts
before relying on them._

Companion: `POLYMARKET_VENUE_EVALUATION.md` (whether we should trade
Polymarket at all) and `ODDS_INGESTION_COVERAGE_MATRIX.md` (all sources).

## Access patterns — read this before writing a client

Novig: Hasura GraphQL, `POST https://api.novig.us/v1/graphql`, anonymous,
no key. Discovered from the Expo bundle at `app.novig.us`; every REST path
on `api.novig.us` returns the string `hello`, a catch-all that hides the
API. Websocket at `wss://api.novig.us/v1/graphql`.

Kalshi: REST, `https://api.elections.kalshi.com/trade-api/v2`, anonymous
for all market data. A key is only needed for trading.

Polymarket — and this is the trap: there are TWO venues.
- `polymarket.com` / `gamma-api.polymarket.com` / `clob.polymarket.com` is
  the OFFSHORE venue. Its own site states "Trading is blocked in the United
  States on polymarket.com." Keyless and rich, but it is a venue we cannot
  trade.
- `polymarket.us` is the CFTC-regulated DCM (QCX LLC d/b/a Polymarket US).
  Public keyless reads live at `https://gateway.polymarket.us/v1/*`.
  `https://api.polymarket.us/*` is the AUTHENTICATED side and returns 401
  "Missing required API key headers" on every path including `/health`.

**Our existing soccer client points at `gamma-api.polymarket.com`** — the
offshore venue. Any session porting it inherits that. See the evaluation doc
§7 for why that matters and what to do.

## Hard-won conventions

Novig prices are PROBABILITIES, not American odds. `outcome.last` sums to
1.0000 across a market's outcomes; it is the last traded price. `available`
is the takeable (ask) side and sums to roughly 1.015–1.02. **EV must use
`available`, never `last`** — `last` overstates edge by about half the
spread on every bet. Same rule as Kalshi's mid-versus-ask.

Kalshi series naming is uniform: `KX{SPORT}{FAMILY}` where FAMILY is one of
GAME, SPREAD, TOTAL, WINS, MATCH, BTTS. Kalshi's legacy integer price fields
return null since ~2026-07; read `yes_bid_dollars` / `yes_ask_dollars` /
`no_ask_dollars` (strings). `floor_strike` is ALREADY the half-point line
for MLB pitcher props and soccer totals — applying a `N − 0.5` adjustment
there double-adjusts. Check per series.

Polymarket US market slugs are `{prefix}-{league}-{...}`: `aec-` for game
markets, `tec-` for futures. Order books are read by SLUG, not id:
`GET /v1/markets/{slug}/book`. Tick size 0.001, minimum trade quantity 1.

Kalshi codes are unique only WITHIN a series, never globally — `LEV` is
Levante in `KXLALIGAGAME` and Levski Sofia in `KXUCLGAME`. Soccer solved
this with a competition-scoped identity table; any repo attaching Kalshi in
a second competition needs the same.

## Novig — open pregame markets, probed 2026-09-20

Flat lines, format: league / total open markets / notable types with count
and 30-day traded volume.

NFL / 959 / TOTAL n=261 $84,893 · SPREAD n=168 $198,234 · RECEIVING_YARDS
n=204 $4,397 · RECEPTIONS n=50 $19,568 · TOUCHDOWNS n=38 $7,870 ·
RUSHING_YARDS n=86 · PASSING_YARDS n=66 · FIRST_TOUCHDOWN_SCORER n=28

NCAAF / 632 / SPREAD n=253 $238,989 · TOTAL n=232 $44,417 ·
CHAMPIONSHIP_WINNER n=127 $1,101 · TEAM_TOTAL n=8 · SPREAD_1H n=7 ·
TOTAL_1H n=4 · MONEY n=1

MLB / 216 / WORLD_SERIES_WINNER n=11 $19,679 · HITS_RUNS_RBIS n=28 $1,094 ·
SPREAD n=10 $1,073 · TOTAL n=10 $852 · RBIS n=37 · RUNS n=33 · HITS n=28 ·
TOTAL_BASES n=21

MLS / 126 / TOTAL n=4 $7,451 · SHOTS_ON_TARGET n=50 $17 · PLAYER_GOALS
n=57 $0 · rest $0

Ligue 1 / 436 / PLAYER_GOALS n=163 · FIRST_GOAL_SCORER n=130 · FOULS n=53 ·
ASSISTS n=39 · SHOTS_ON_TARGET n=38 · SPREAD n=3 $20 · everything else $0

La Liga / 270 / FIRST_GOAL_SCORER n=64 · PLAYER_GOALS n=42 · ASSISTS n=34 ·
GOALS_ASSISTS n=34 · FOULS n=33 · TOTAL n=12 · all $0

Serie A / 237 / PLAYER_GOALS n=57 · SHOTS_ON_TARGET n=49 ·
FIRST_GOAL_SCORER n=42 · ASSISTS n=41 · FOULS n=37 · all $0

WNBA / 57 / REBOUNDS n=14 · FIRST_BASKET n=11 · POINTS n=9 $513 ·
THREE_POINTERS_MADE n=7 · ASSISTS n=7 · DOUBLE_DOUBLE n=6

The shape to take from this: Novig's liquidity is concentrated in US
in-season game lines. NFL and NCAAF spreads and totals are the real books.
Soccer is listed broadly and traded at zero — and what IS listed for soccer
is overwhelmingly player props, not the 1X2/totals/spreads we publish.

## Kalshi — open markets by series, probed 2026-09-20

Flat lines, format: sport / series stem / open market counts by family.

NFL / KXNFL* / GAME=62, SPREAD=417, TOTAL=304, WINS=511
NBA / KXNBA* / GAME=6, WINS=312
MLB / KXMLB* / GAME=80, SPREAD=111, TOTAL=161
WNBA / KXWNBA* / GAME=26, SPREAD=63, TOTAL=45, WINS=8
CFB / KXNCAAF* / GAME=524, SPREAD=787, TOTAL=608, WINS=587
EPL / KXEPL* / GAME=12, SPREAD=16, TOTAL=24, BTTS=4
La Liga / KXLALIGA* / GAME=15, SPREAD=20, TOTAL=30, BTTS=5
Serie A / KXSERIEA* / GAME=15, SPREAD=20, TOTAL=30, BTTS=5
Bundesliga / KXBUNDESLIGA* / GAME=9, SPREAD=12, TOTAL=18, BTTS=3
Ligue 1 / KXLIGUE1* / GAME=9, SPREAD=12, TOTAL=18, BTTS=3
UCL / KXUCL* / none open at probe time
ATP / KXATP* / none open at probe time
WTA / KXWTA* / MATCH=18
PGA / KXPGA* / none open at probe time

Changes worth knowing since the 2026-08-13 probe: Bundesliga has opened
(was zero, preseason). Soccer SPREAD, TOTAL and BTTS are now live across all
five domestic leagues, where in August only GAME traded. CFB is now the
largest surface on the venue by market count.

## Polymarket US — sport coverage, probed 2026-09-20

`GET /v1/sports` returns 265 sport slugs. Every sport we model is present
and `isOperational: true`. Slugs for ours:

nfl, nba, mlb, wnba, cfb, cbb, nhl, atp, wta, epl, lal (La Liga), sea
(Serie A), bun (Bundesliga), lg1 and lig2 (Ligue 1), ucl, uel, mls, and
golf as separate event slugs: masters, theopen, pgacham, lpga, dpwt, liv.

`automaticResolution` is false for all of ours — resolution is
rulebook-based, not automated. Named resolution sources are published for
the majors: nba.com, nfl.com, mlb.com, ncaa.com, nhl.com, ufc.com,
mlssoccer.com.

Open market mix at probe time skewed heavily to futures: of 500 open
markets sampled, 496 were `sportsMarketType: futures`. Open EVENTS by league
prefix: nfl 240, cfb 83, mlb 21, ufc 8, nba 8, nhl 4, mls 3.

## Useful endpoints, literal paths

Novig, all POST to `https://api.novig.us/v1/graphql`:
- events: `{ event(where: {is_visible_pregame: {_eq: true}}) { id description league scheduled_start status } }`
- markets with prices: `{ market(where: {status: {_eq: "OPEN"}}) { type strike volume is_consensus outcomes { type description last available } } }`
- Do NOT query `order` or `fill`: they return other users' trade records.
  We need prices, not the tape. `order_aggregate` also exceeds Hasura's 4s
  limit and is not viable anyway.

Kalshi, GET on `https://api.elections.kalshi.com/trade-api/v2`:
- `/markets?series_ticker={KXSTEM}{FAMILY}&status=open&limit=200` (cursor-paged)
- `/events`, `/series`, `/markets/{ticker}/orderbook`

Polymarket US, GET on `https://gateway.polymarket.us/v1`:
- `/sports` — all sports and leagues, with resolution source
- `/markets?closed=false&limit=500` — open markets
- `/events?closed=false&limit=500` — open events with nested markets
- `/markets/{slug}/book` — full order book, bids and offers with quantities
- `/markets/{slug}/bbo` — best bid/offer
- `/markets/{slug}/settlement` — settlement price
- price history and search endpoints also exist; see docs.polymarket.us

All three are keyless for reads. None of the above requires an account.
