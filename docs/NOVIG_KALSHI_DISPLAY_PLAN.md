# Novig + Kalshi display plan

_2026-08-13, Books/Odds session. All numbers below are from live probes run
this session from cloud egress; re-probe before acting on any of them, since
exchange liquidity moves fast._

## 0. The headline: no browser scraping needed

The premise for this work was that Novig would be scraped through Claudia's
browser/computer access. **It does not need to be.** Novig's app is an Expo
web build talking to a **Hasura GraphQL endpoint that answers anonymously**:

```
POST https://api.novig.us/v1/graphql        # also wss:// for subscriptions
{"query":"{ __typename }"} -> {"data":{"__typename":"query_root"}}
```

Discovered from the app's own JS bundle (`app.novig.us` →
`/_expo/static/js/web/index-*.js`, 17MB, contains
`wss://api.novig.us/v1/graphql`). Note `api.novig.us` returns the string
`hello` to every REST path — a catch-all that makes the API look absent
unless you find the GraphQL route.

Anonymous introspection is enabled: 66 query-root fields, including `event`,
`game`, `market`, `outcome`, `competitor`. No key, no login, no cookie.

**Why this matters:** a keyless HTTP adapter is cheaper, faster, fixture-
testable, and far more reliable than driving a headless browser. It also
runs anywhere, including CI and this cloud session — browser scraping would
have pinned odds ingestion to one Mac Mini.

## 1. The risk that actually matters: protect the betting account

Claudia is **placing real money** on Novig. That makes the data path a
liability, not just an engineering choice.

**Rule: the read path is never authenticated.** No auth header, no session
cookie, no account-derived token, ever — not "for now", not "just to test".
If Novig ever rate-limits or blocks the scraper, an anonymous client costs
us a data feed; an authenticated one risks the account that holds real
funds and open positions.

Concretely:
- Distinct `User-Agent` identifying the client, not a spoofed browser one.
- Conservative polling (see §5); no burst introspection in production.
- **Never query `order` or `fill`.** They return other users' trade records
  anonymously (`fill` fields: `cost created_at id isTaker isVoided isWash
  marketId orderId outcomeId qty takerId version` — pseudonymous UUIDs, no
  names or emails, but still other people's activity). We need prices, not
  the tape of who traded. `order_aggregate` also times out at Hasura's 4s
  limit, so it is not a viable data source anyway.
- Read `market` / `outcome` / `event` / `competitor` only.

**ToS posture: gray, same class as Bovada/FanDuel.** Keyless and
unauthenticated, but an undocumented internal API rather than a published
data product — not a sanctioned feed. Fine for internal modelling under the
org's existing two-surface posture. **Publishing Novig prices on the
public-read bucket is a separate decision and needs Doyle's explicit call**
(§6), exactly like the Pinnacle question.

## 2. Novig is an EXCHANGE, not a sportsbook

Decisive evidence — `outcome.last` sums to **exactly 1.0000** across
essentially every market sampled:

```
[MLB] Cincinnati Reds @ San Francisco Giants  TOTAL  vol=$161,882
     Over 7.5    last=0.48   available=0.48
     Under 7.5   last=0.52   available=0.535    -> last sums to 1.0000
```

Novig is peer-to-peer with no vig (hence the name). Therefore it inherits
the **existing exchange treatment wholesale** — this is the soccer repo's
`frame.EXCHANGE_BOOKS` design working exactly as intended:

- **Never de-vig it.** The price is already a probability.
- **Never include it in the sportsbook consensus vector.**
- Renormalize when a market's sides do not sum to exactly 1 (observed: one
  spread summed to 1.0020, so this is not hypothetical).
- Style it as an exchange in the UI, never as another book.

Adding it is adding one entry to `EXCHANGE_BOOKS` — one code path,
deliberately, so it cannot grow a parallel implementation that drifts.

## 3. Price semantics — the trap to encode before anything ships

Two price fields, and **using the wrong one silently inflates every EV**:

| field | meaning | sums to |
|---|---|---|
| `last` | last traded price | **1.000** |
| `available` | the price you can take right now (ask side) | **~1.015–1.02** |

`available` is consistently ≥ `last`. The gap between them is the real
cost of trading, and it is what EV must be computed against.

**`last` is the fair-value read; `available` is the executable read.**
Quoting EV off `last` overstates edge by roughly half the spread on every
single bet — the identical mistake as using a Kalshi mid instead of its
ask, which the org already has a rule about. Encode it in the adapter, not
in a reviewer's memory.

The good news: Novig's executable overround is **1.5–2%** on liquid markets
versus **4–6%** on the sportsbooks we carry. Where Novig is liquid, it is
the sharpest fair-value reference available to us — sharper than our
de-vigged Bovada/FanDuel consensus. That has real implications for CLV
benchmarking and for soccer's market track, but that is a **modelling
decision owned jointly with the sport sessions**, not a Books/Odds call.

Also: `is_consensus` (bool) appears on `market` and its semantics are
**unknown** — it is true on some high-volume markets and false on others.
Events also carry `optic_odds_id` and `unabated_id`, so Novig is itself
seeding lines from commercial aggregators (OpticOdds, Unabated).
`is_consensus` plausibly distinguishes a seeded line from an organically
traded one. **Do not guess** — establish it before using it as a filter.

## 4. Coverage reality — measured, and narrower than it first looks

500 pregame-visible events (query limit), spanning nearly our whole stack:

| league | events | | league | events |
|---|---|---|---|---|
| MLB | 154 | | PGA | 17 |
| ATP | 110 | | MLS | 16 |
| WTA | 106 | | NCAAF | 10 |
| WNBA | 30 | | La Liga / Ligue 1 / EPL / Serie A / UCL / Europa | 14 total |
| UFC | 23 | | NBA | 0 (offseason) |
| NFL | 18 | | | |

**But listed ≠ liquid, and the gap is enormous.** Across 800 sampled open
pregame markets, counting only those with a two-sided live quote:

| league | markets | two-sided quoted | % | median spread | volume |
|---|---|---|---|---|---|
| MLB | 259 | 18 | **7%** | 3.50% | $12,710 |
| WTA | 224 | 12 | **5%** | 10.00% | $500 |
| ATP | 188 | 15 | **8%** | 8.50% | $0 |
| EPL | 121 | **0** | **0%** | — | $0 |
| PGA | 8 | **0** | **0%** | — | $45 |

Two conclusions, both of which change the plan:

1. **~92–95% of listed markets are empty shells.** This is handover trap #7
   (exchange liquidity is bimodal) at full strength. The top of the book is
   excellent — the highest-volume MLB and WNBA game lines quote 1.5–2% with
   $25k–$160k volume — and it falls off a cliff immediately below that.
2. **Soccer has nothing usable.** All 121 EPL markets are two-outcome
   **player props** (FOULS, ASSISTS, PLAYER_GOALS, FIRST_GOAL_SCORER,
   SHOTS_ON_TARGET, GOALS_ASSISTS), zero quoted, and **no 1X2, totals or
   spreads are listed at all**. The markets we actually publish for soccer
   do not exist on Novig today.
3. **Tennis is listed but too wide.** 5–8% quoted at 8.5–10% spreads is
   *worse* than the de-vigged sportsbook price we already publish. It would
   be a downgrade presented as an upgrade.

The usable set today is **MLB and WNBA game lines (moneyline / spread /
total), top-of-book only**. NFL will likely join when the season starts —
136 spread and 213 total markets are already listed at near-zero volume,
which is what preseason looks like.

## 5. Recommended sequencing

This deliberately follows the **liquidity**, not the plumbing. Soccer and
tennis have the exchange machinery already built (`EXCHANGE_BOOKS`, market
board, `isExchangeBook`) and would be fastest to wire — but showing a
quote-shell is worse than showing nothing, because a wide or stale exchange
price presented next to a sharp book price reads as signal when it is noise.

**Phase 0 — shared adapter (prerequisite, ~1 day).**
`predictium_odds/books/novig.py`: keyless GraphQL client, `Quote` mapping,
`is_exchange=True`, `last` vs `available` kept distinct, hard liquidity gate
(both sides quoted + spread ceiling + volume floor), `SourceReport` for
health. Fixture-tested. Plus a `CoverageSpec` per sport so a Novig outage is
loud rather than a silent zero — the failure that started this whole mission.

**Phase 1 — MLB and WNBA (where the money actually is).**
Both already run in-repo fetchers, so this pairs naturally with the standing
"swap repos onto the shared adapters" work rather than duplicating it. WNBA
is the smaller surface and is already priority #1 on that list — do it first
and let it prove the pattern.

**Phase 2 — NFL, at season start.** Re-probe rather than assume; the listed
markets suggest it will come alive.

**Phase 3 — tennis, golf, soccer: NOT YET.** Re-probe monthly. Attach when a
league clears a stated liquidity bar (proposal: ≥30% of published fixtures
carrying a two-sided quote inside a 4% spread). Committing to that bar in
advance stops "it exists, so let us show it" from winning later.

**Frontend cost is genuinely small.** Both tennis and soccer already render
any exchange key generically from a per-book map. In
`Predictium_Front_End/lib/book-format.ts` it is two lines — add
`novig: "Novig"` to `KNOWN_BOOK_NAMES` and `"novig"` to `EXCHANGE_BOOKS` —
and the existing "(exchange)" styling and teal treatment apply
automatically. The MLB/WNBA sections would need per-book market rendering
built (they do not have the tennis/soccer market board yet); that, not the
Novig plumbing, is the real frontend work in Phase 1.

## 6. Kalshi — what actually changed, and what to do next

Worth being precise, because "we have the Kalshi API working now" can mean
two different things:

- **Kalshi market data has always been keyless** and is already in
  production for NFL win totals, tennis ML, MLB pitcher ladders, and soccer
  (as of this session, 32 of 40 published matches). Displaying Kalshi
  prices has never required a key and still does not.
- **What a key unlocks is trading** (order placement, fills, positions) and
  possibly higher rate limits. That is a Claudia/betting-execution
  capability, not a display one.

So the Kalshi half of "start showing prices" is **not an access problem, it
is a coverage problem**. The real gaps, in order:

1. **WNBA and CFB** have Kalshi series but no attach — and WNBA is exactly
   where Novig is liquid too, so one repo gets both exchanges at once.
2. **NBA** publisher is staged but the plist is unloaded (season-start
   checklist, ~late Oct).
3. **Tennis spread/total** series exist but list zero markets — same
   re-probe discipline as Novig's thin leagues.

**Do not let the new key leak into the display path.** The read path stays
keyless so it keeps working everywhere, and the trading credentials stay
with Claudia. Same separation as the Novig account rule in §1.

## 7. Decisions needed from Doyle

1. **May Novig prices be published to the public-read bucket**, or are they
   internal-only like Pinnacle? Gray-ToS keyless source, same class as
   Bovada/FanDuel which we do publish — so the precedent says yes, but it is
   your call to make explicitly rather than by default.
2. **Confirm the liquidity bar** for attaching a league (proposed above), so
   thin markets are excluded by a rule rather than by argument each time.
3. **Fair-value reference question** — if Novig's 1.5–2% executable
   overround beats our de-vigged sportsbook consensus, should it become the
   CLV/fair-value anchor where it is liquid? This is a modelling decision
   for the sport sessions; Books/Odds should not decide it alone.
