# Books & Odds session — role handover

_Last updated 2026-08-13. Started by the session that built the shared odds
layer; maintained by each session that holds the Books/Odds role (any sport)._

**If you are reading this off `main`, check the date above.** The 2026-08-02
Odds API scoping amendment (§3) and everything after it lived only on
unmerged branches for eleven days, so a session that read `main` in good
faith got the superseded unconditional ban. Verify a policy claim against
the newest branch before acting on it, and prefer landing doc changes
promptly over accumulating them on a branch.

Read this first, then `docs/ODDS_INGESTION_COVERAGE_MATRIX.md` for the
source-by-source detail. Both live here in `predictium-api` because odds work
is cross-repo by nature — no single sport repo owns it.

---

## 1. What this role is

You own **market data ingestion** for Predictium: getting prices from books
and exchanges into each sport model's pipeline, correctly, and making sure a
broken source is impossible to miss.

You are **not** the modeling session. You don't own predictions, EV
thresholds, or pick logic — you own the market side of the boundary and the
data contract where the two meet. Sport sessions consume your quote frames;
schema friction between you and them is a **joint decision**, not a
unilateral edit (see §6).

Concretely, the work splits four ways:

1. **Adapters** — per-source clients that fetch and normalize prices.
2. **Shared math** — odds conversion, de-vig, best-line, consensus,
   main-line alignment.
3. **Health monitoring** — every fetch reports; drift is loud.
4. **Coverage research** — probing new sources, judging ToS posture,
   telling Doyle plainly what's worth adding and what isn't.

---

## 2. The shared layer

`predictium_odds` lives in this repo at `predictium_odds/` and is
pip-installable as a subdirectory package. Read its `README.md` before
touching it.

```
pip install "git+https://github.com/doyledettro-maker/predictium-api.git@<sha>#subdirectory=predictium_odds"
```

Every model repo pins it **by commit SHA**, currently
`2da418a0712cf5c0054def6637df8dfc8d8a8b19`. Never float a branch.

**`odds-v0.1.0` EXISTS — corrected 2026-08-13.** Earlier revisions of this
doc (and the kickoff prompt derived from it) said the tag was unminted and
sat on Claudia's task list. It does not: `git rev-list -n1 odds-v0.1.0`
resolves to `2da418a`, the exact SHA every repo already pins, as an
annotated tag dated 2026-07-25. Claudia minted it and the doc never caught
up. Verify before repeating the claim — `git ls-remote --tags origin`.

That means the remaining work is the reverse of what was written: the repos
pin a raw SHA that a real tag now aliases, so each can move to
`@odds-v0.1.0` at its next dependency touch. Cosmetic, not urgent — the
pinned SHA and the tag are the same commit, so nothing is mispinned today.

| Module | Contents |
|---|---|
| `schema` | `Quote` — one priced side of one market from one source |
| `oddsmath` | conversions + de-vig: multiplicative, proportional, **Shin** (ML), **power** (n-way futures) |
| `lines` | `best_line`, `consensus`, **`align_main_line`** |
| `health` | `SourceReport` / `CoverageSpec` / `evaluate` |
| `books.*` | bovada, fanduel, kalshi, espn (DK lines), pinnacle |

Note the layer is currently used for **health reporting everywhere** and
**adapters in NBA only**. The other repos still run their own in-repo
fetchers. Swapping them onto the shared adapters is deliberate,
one-repo-at-a-time work behind a flag with a parity check (§7).

---

## 3. Hard invariants — do not weaken these

These are org rules, several of them written in blood.

- **The Odds API: banned from all ongoing ingestion; historical backfill
  only, per task, on Doyle's say-so.** Amended 2026-08-02 (Doyle) — the
  previous rule was an unconditional ban, and NBA's removal as the last
  ongoing consumer still stands. What changed is narrow, and the two halves
  are not negotiable against each other:
  - **Never in a live path.** No adapter in `predictium_odds`, no client in
    any repo's `books/` package, no import reachable from a publisher, cron,
    launchd job, or scheduled workflow. The live tape stays keyless
    direct-book + exchange. A PR adding it to an ingestion path is still
    refused on sight.
  - **Permitted:** one-off, human-initiated pulls of *historical* odds for a
    specific modelling or backtest task, authorized per task. Not a standing
    grant — "we may need it again" is not authorization for the next pull.
  - **Every pull lands as a committed, reproducible artifact** — script,
    cached payload, and provenance (endpoint, params, fetch timestamp,
    credits spent) — the same standard as any Claudia bulk pull. A number
    that can't be re-derived from a committed artifact didn't happen.
  - **Key via env / GitHub Actions secrets only**, never committed. Note this
    is the first non-keyless source in the stack; the "everything is keyless"
    assumption below no longer holds without qualification.
  - **Redistribution boundary — the one that bites.** Treat Odds API data
    exactly like Pinnacle: INTERNAL-ONLY, `redistributable=False`. No
    Odds-API-derived price may reach a published artifact or the public-read
    predictions bucket, attributed or not. Model metrics *computed against*
    it (log loss, CLV, calibration) are publishable; the prices themselves,
    and any per-book table containing them, are not. This is the live risk:
    several repos publish `backtest.json` objects measured against closing
    lines straight to the public bucket, so a historical backfill that
    silently becomes a published benchmark column is a licensing exposure,
    not just a style violation.
- **Keys via GitHub Actions secrets / env only.** Never committed. Note that
  every source we use today is keyless — if a new one needs a key, that's a
  Doyle decision, not a default.
- **The predictions bucket is public-read.** Nothing licensed, private, or
  provider-restricted may ever be uploaded to it.
- **Pinnacle is INTERNAL-ONLY.** Its adapter marks every quote
  `redistributable=False`. Pinnacle prices feed internal fair-value/consensus
  and must never reach a published artifact or the public bucket. Approved by
  Doyle on exactly those terms.
- **Main-line alignment before any price comparison.** An alt-line ladder
  (Kalshi strikes, book alt lines) is only comparable to the market at the
  entry equivalent to the consensus main line. `lines.align_main_line` /
  `books.kalshi.select_aligned_contract` return `None` when there's no
  equivalent — surface that loudly, **never** substitute a nearby strike. An
  off-line price compared against a main line silently corrupts every EV and
  CLV number downstream.
- **Never de-vig an exchange.** Kalshi prices are already probabilities
  (`is_exchange=True`).
- **Fail soft, but never silent.** A dead source degrades gracefully AND
  raises a detectable signal. The whole mission exists because a FanDuel
  break returned empty quietly for weeks.
- **Identity before modeling.** Book/exchange names resolve through committed
  alias tables. No fuzzy matching at runtime, ever. An unresolved name simply
  doesn't join — that's correct behavior, not a bug to paper over.
- **Per-book rows, never pre-merged.** CLV and settlement need each source's
  own price. Consensus is a derived view, not a replacement.
- **Pre-game only.** In-play prices never reach the tape. Each source needs
  its own gate (book `live` flags; for Kalshi, which trades through events
  and has no per-quote flag, gate on scheduled start time).

---

## 4. Traps we've already hit

Every one of these cost real time or shipped bad data. Check them first when
something looks wrong.

1. **Kalshi's legacy price fields return null** (since ~2026-07). Read
   `yes_bid_dollars` / `yes_ask_dollars` / `no_ask_dollars` (strings), not
   `yes_bid` / `yes_ask` (ints). The old NBA publisher broke on exactly this.
2. **Kalshi ladder equivalence:** a book's "Over L" is Kalshi's "≥ L + 0.5".
   Integer book lines can push and have **no** binary equivalent — refuse
   them. For MLB pitcher props, `floor_strike` is *already* the half-point
   line (a "17+" contract carries 16.5) — don't double-adjust.
3. **FanDuel restructures lobbies silently.** Season futures left the
   `customPageId` lobby and now live on per-team website pages
   (`/teams/_next/data/{buildId}/{sport}/{slug}/odds.json`). The build ID is
   deployment-specific — **discover it from a team page's HTML every run**,
   never hard-code.
4. **Derive team slugs from the canonical name table**, never by inverting a
   name→abbr map. Those maps carry relocation-era aliases (St. Louis Rams,
   San Diego Chargers, Oakland Raiders) that clobber current franchises —
   three 404s a run, fanduel=28/32, silent for days.
5. **FanDuel's NBA lobby lists NBA 2K esports sims** ("Denver Nuggets
   (ENCORE)"). Strict full-name matching only; a fuzzy resolver would ingest
   video-game lines as real NBA odds.
6. **Bovada's slate feed already contains props.** MLB's scraper claimed
   "Bovada does not expose player props via API" and skipped a Pitcher Props
   display group sitting right there — pitcher-outs had zero rows for months.
   Check the payload before believing a docstring.
7. **Exchange liquidity is bimodal.** Majors quote tight; small events are
   quote-shells (2¢/71¢). Always gate on bid-ask spread and, for outright
   boards, a minimum count of genuinely two-sided contracts.
8. **Partial coverage is drift.** 28/32 teams is the same failure class as
   zero. Warn on `0 < n < expected`, not just empty.
9. **Watchdog gates must key on ACTIVE state, not files-ever.** A "have we
   ever received data?" check goes permanently silent after the first
   success. Ask "is there currently an unstarted X?" instead.
10. **FanDuel host drift:** repos were split across `sbapi.nj` and
    `sbapi.mi`. Unify on `nj` unless a sport's markets are genuinely
    geo-gated.
11. **Exchange short codes are unique per SERIES, not globally — and a
    collision resolves to the WRONG club, confidently.** Found live in
    soccer 2026-08-13. Kalshi's `LEV` is Levante in `KXLALIGAGAME` and
    Levski Sofia in `KXUCLGAME`; `PAR` is Parma in Serie A and Paris FC in
    Ligue 1. The soccer repo stores one globally-unique `kalshi_code`
    column, so a LaLiga Levante contract resolved to `bul-levski-sofia`
    and the event reported as *fully resolved*. Nothing mis-priced only
    because the fixture join separately required the competition to match.
    **This is the failure mode identity discipline is supposed to prevent,
    and it passed every fixture test**, because the two codes never appear
    in the same fixture file. Two lessons worth carrying to every repo
    attaching an exchange:
    - **Scope the alias to whatever namespace the source actually uses.**
      A flat "book name → team" column silently assumes global uniqueness.
      Check that assumption against a second series before trusting it.
    - **"Resolved" is not "resolved correctly."** A resolver that returns a
      valid id for a wrong club is worse than one that returns nothing.
      Constrain the resolution by something independent (soccer now checks
      the club's country against the competition's own country, derived
      from the competition's `espn_slug` so it cannot drift) and make the
      refusal loud.

---

## 5. Where things stand (2026-07-27)

**Sources in production:** Bovada + FanDuel (all sports), Kalshi (NFL win
totals, NBA publisher staged, tennis ML, MLB pitcher ladders), ESPN/DraftKings
(NBA game lines). Pinnacle adapter exists, internal-only, not yet wired.

**Health monitoring is live in all six model repos** — each prints a
`book-health [sport]` line every run and warns per underperforming source.
In-season all-sources-down exits non-zero in NBA/WNBA/MLB.

**Per sport:**

- **NFL** — win totals live from FanDuel (team pages) + Bovada board +
  Kalshi `KXNFLWINS`, all 32/32. Kalshi anchor published on the power board
  (`kalshi_prob_over` / `kalshi_implied_wins` / `kalshi_ticker`) and rendered
  as a teal "Kalshi (exchange)" pill.
- **NBA** — off The Odds API entirely. New direct-book fetcher (Bovada +
  FanDuel + ESPN/DK, consensus row). Kalshi publisher rebuilt with
  legacy-identical gameIds; plist staged but **not loaded** — season-start
  checklist in `nba_prediction_model_2026/ODDS_MIGRATION_2026-07.md`.
- **Tennis** — Kalshi attached as an ML-only, attach-only exchange source;
  per-book EV published inside `market.books{}`; FE renders a full market
  board. Futures draw-coverage watchdog compares Kalshi outright boards
  against committed draws.
- **MLB** — pitcher outs + earned runs from Bovada, multi-line FanDuel O/U
  parse, Kalshi `KXMLBOUTS`/`KXMLBKS` ladders as a third prop source.
- **WNBA / CFB** — health monitoring; adapters unchanged.
- **Golf** — new to this role; see §5a below. No shared layer, no health
  monitoring, books at probe stage only.
- **Soccer (2026-08-13 update): Kalshi now reaches the published board.**
  The domestic series opened (numbers in §7.3), 74 `kalshi_code` values
  are committed, and `kalshi_code` is 94/234. Live domestic resolution
  **45/49 events** (EPL 10/10, LaLiga 18/20, SerieA 9/10, Ligue1 8/9).
  Three clubs — Levante, Parma, Paris FC — are deliberately uncommitted
  because their codes collide under a globally-unique column (trap #11);
  that is escalated as Q6 in the soccer repo's
  `handoffs/reports/0005d-ucl-questions.md` as a schema decision for its
  Planner, with a recommendation to competition-scope the column.
  **No contract or frontend change was needed:** Kalshi flows through the
  existing generic `market.books{}` map (`publish/market.py::_books_breakdown`
  groups over all books; only the *consensus vector* excludes exchanges),
  and the frontend renders any exchange key via `lib/book-format.ts`
  (`isExchangeBook`) + `orderedBookNames`. Worth knowing for the other
  repos' Kalshi attaches: if the frontend already has a generic per-book
  map, attaching an exchange is a backend-identity job, not a UI job.
  Still owed: nobody has run `publish_soccer.py` against a built `data/`
  to see Kalshi in an actual payload — `data/` is gitignored so a cloud
  session cannot. That is a Claudia task.
- **Soccer** — shipped 2026-08-02 (spec `0005b-market-clients`, branch
  `claude/books-odds-session-8rndry`). Keyless Bovada + FanDuel +
  Polymarket clients feeding ONE quote frame:
  `soccer_model/data/books/frame.py::collect_quotes(fixtures, *,
  from_cache=False)` -> a pinned 11-column per-book DataFrame, with a
  schema-pin test as the executable contract with spec 0005c. Shin de-vig
  via the repo's own 0002b machinery; exchange mids renormalized, never
  de-vigged; best price is a DERIVED view (`frame.best_prices()`), so
  per-book rows are never pre-merged. Live at ship: 2,661 quotes / 48
  matches / 5 leagues (bovada 1,849, fanduel 812, polymarket 0 — it lists
  no 2026-27 match-winner market yet).
  Three things worth carrying to the next sport: pre-game gating was
  entirely absent until this spec (both books now gate on their own flags
  — Bovada `event.live`, FanDuel `inPlay`/`marketStatus`); sportsbook rows
  carried no kickoff, so the +/-6h fixture window could only be applied to
  Polymarket; and Bovada's team totals were sitting unparsed in a payload
  we were already fetching (the MLB pitcher-props lesson, again). One
  defect: FanDuel hardcoded `main: True` on every AH rung, so 46/48 live
  matches carried two contradictory "main" lines — it publishes one
  marketType per handicap, so `HOME_TEAM_-1.5` and `AWAY_TEAM_-1.5` land
  on opposite sides. `fanduel.flag_main_ah()` now picks closest-to-balanced.
  **This session formally owns `soccer_model/data/books/**`** (clients,
  quote frame, book identity columns) per that repo's
  `handoffs/multi_session_workflow.md`; other sessions treat the package
  as read-only and frame friction is a joint questions-file event.

**Blocked / pending:**

- Mac Mini prod checkouts must `git pull` for tennis + MLB changes to reach
  the live tape (Claudia's task list).
- ~~`odds-v0.1.0` tag unminted~~ — **done**, see §2 (corrected 2026-08-13).
- Soccer: quote frame shipped and all rulings ratified; the Coder is
  merging that branch under spec 0005c rev 2. Kalshi soccer is a GATED
  backlog item owned by this role (§7.3).
  **Update 2026-08-13: ungated and shipped** — see §5 "Soccer" below.

### 5a. Golf — new to this role, diagnosed 2026-08-13

Golf is the one genuinely new sport repo and the only one with **no
book-health monitoring at all** — a dead source there fails silently today.

- **Zero `predictium_odds` adoption**: no import anywhere, no health
  reporting. Every other model repo has at least the soft-import health line.
- **No book clients in the pipeline.** `golf_model/ingest/` holds only
  `espn.py` and `espn_core.py`. Books exist purely as probes under
  `scripts/probes/` (`probe_books.py`, `probe_kalshi.py`,
  `probe_polymarket.py`, `probe_datagolf.py`).
- **Its own de-vig copy** in `golf_model/markets/devig.py` + `clv.py` —
  another divergent implementation against shared `oddsmath`. Building
  golf's clients on the shared adapters avoids creating a seventh.
- **The Odds API usage there is correctly contained** and worth copying as
  the reference shape for the amended policy: `scripts/ingest_theoddsapi_golf.py`
  writes to `data/internal/theoddsapi/`, the manifest carries
  `endpoint_policy: "historical_only"`, rows carry a `license_class`
  column, provenance is per-call (sha256, quota, credits — 1,117 actual vs
  1,080 planned), the key comes from the keychain via subprocess, and
  nothing in `ops/jobs/` or `golf_model/` references it.
- **Live capture evidence** (FedEx St. Jude, 2026-08-10, both books,
  69/70 runners each): Bovada 47.4% overround (63rd pctile of majors),
  FanDuel 39.0% (50.7th). Kalshi golf depth is still **unmeasured, not
  absent** — the only probe ran on a quiet week (2026-08-08, `degraded:
  true`, most series 0 open markets). Re-probe on a live tournament week.
- Note when reading `fixtures/probes/books/summary.json`: its
  `licensing_verdict: BLOCKED` on Bovada and FanDuel is the **strict
  pre-ratification reading, deliberately kept visible**. It was since recast
  under the ratified two-surface posture. Not a live blocker.
- Golf runs **no GitHub Actions** (org cost decision) — Mac Mini launchd
  only — and has 4-agent governance in `handoffs/multi_session_workflow.md`.
- Target **T1 2026-10-15**: odds/CLV logging live on Bovada/FanDuel/Kalshi.

---

## 6. Working with other sessions

Predictium runs one session per sport plus this role, coordinated through
each repo's `ROADMAP.md` and, where it exists, a `handoffs/` directory with a
multi-session workflow doc. **Read the target repo's governance docs before
touching anything** — some repos have formal spec/review flows with assigned
roles, and your branch and boundary are defined there.

Rules that have worked well:

- **Contract changes land together.** A backend field, its
  `*_DATA_MODEL_DESIGN.md` entry, and the frontend `types/*.ts` mirror change
  in the same session — never one without the others.
- **Schema friction is a joint event.** If a sport's quote frame can't
  represent what a source offers, that goes in the repo's questions/handoff
  file for the owning session, not into a unilateral edit.
- **Claudia** (Doyle's local agent on the Mac Mini) owns everything that
  needs local egress, AWS, or the prod checkouts: bulk pulls, launchd agents,
  tag pushes, deploys. Anything she produces must land as a committed,
  reproducible artifact. Give her explicit numbered task prompts.
- **Report negatives with numbers.** "Bovada prices only Grand Slams — the
  full tennis futures list is 6 events, all Slams" is useful; "no coverage"
  isn't.

---

## 7. Standing next steps

In rough priority order:

1. **Swap per-repo fetchers onto the shared adapters**, one repo per session,
   behind an env flag with the current fetchers as fallback, and a side-by-side
   parity check before flipping. Pattern documented in the package README.
   Suggested order: WNBA (smallest surface) → CFB → MLB → tennis → NFL.
2. **Pinnacle into the internal fair-value anchor** — it's the sharpest
   consensus reference available and currently unused. Internal only.
3. **Kalshi soccer (UCL) — GATED, owned by this role, contract questions
   already answered.** Do NOT start before both triggers fire: spec 0005c
   stable AND UCL slates publishing. A spec will be issued then. The
   Planner's 2026-08-02 ruling settled everything that used to block it:
   (a) "the exchange's 1X2" = the three binary mids renormalized to sum
   to 1 — identical to the Polymarket treatment, and now the ONE rule for
   all exchanges (renormalized, never de-vigged, never in best price,
   shown under `market.books.{exchange}` with exchange styling);
   (b) settlement aligns by construction — the published soccer 1X2 IS
   the regulation-time (90' + stoppage) result, so `KX*GAME` matches;
   record that in the adapter docstring, and note "to advance" is a
   different market, out of scope; (c) `KXUCLSPREAD` is a strike ladder,
   so the org's absolute alignment rule applies verbatim — consensus main
   line first, fail loud on no equivalent rung, never nearest-strike;
   (d) gate on bid-ask width before quoting.
   Coverage evidence (live probe 2026-08-02, preserve for the spec):
   `KXUCLGAME` 30/30 two-sided across 10 fixtures (median spread 1c),
   `KXUCLTOTAL` 60/60 (1c), `KXUCLSPREAD` 34/40 inside 10c (six
   quote-shells, one at 2c/78c), `KXUCLBTTS` 10/10 (2c). All five
   domestic series (`KXEPLGAME`, `KXLALIGAGAME`, `KXBUNDESLIGAGAME`,
   `KXSERIEAGAME`, `KXLIGUE1GAME`) list ZERO open markets in preseason —
   re-probe when the leagues start rather than assuming they stay empty.
   **DONE 2026-08-13 — they opened, and re-probing was right.** Live:
   `KXEPLGAME` 30 markets/10 events, `KXLALIGAGAME` 60/20 (+ TOTAL 42/7,
   SPREAD 28/7, BTTS 7/7), `KXSERIEAGAME` 30/10, `KXLIGUE1GAME` 27/9,
   `KXUCLGAME` 21/7. `KXBUNDESLIGAGAME` is still 0 — Bundesliga simply
   starts later, so that one is a quiet no-op, not a break. 74 domestic
   `kalshi_code` values committed; see §5 "Soccer" for the identity trap
   this exposed.
4. **Kalshi spread/total for tennis** — the series exist
   (`KXATPGAMESPREAD`, `KXATPGAMETOTAL`) but list zero markets. When they
   open, the alignment machinery is already in place.
5. **NBA season-start checklist** (~late Oct) — load the staged plist, delete
   the legacy agent, eyeball the first slate.
6. **New sources** are a research-then-propose task, not a build task. The
   matrix has the current triage: DK/BetMGM/Caesars are bot-walled everywhere
   probed; Circa is licensed-vendor-only; **Betfair Exchange** is the one
   genuinely interesting unexplored candidate (documented API, deep
   soccer/tennis liquidity, but account + non-US geo — a business decision).

---

## 8. If you're the SOCCER Books/Odds session

**This section used to be a "how to get started / you may not have repo
access" brief. That is all resolved — the repo is in scope, the work
shipped 2026-08-02, and every open question was ratified.** What follows
is what you actually need to know now.

**You own `soccer_model/data/books/**`** — clients, quote frame, book
identity columns — per that repo's `handoffs/multi_session_workflow.md`.
Other sessions treat the package as read-only. Read the repo's
`CLAUDE.md`, `ROADMAP.md`, and that workflow doc before touching
anything; they assign roles and branches and they bind you.

**The seam you defend.** `frame.collect_quotes()` returns a pinned
11-column, strictly per-book frame; spec 0005c consumes it verbatim.
`tests/test_quote_frame.py::test_schema_pin` is the executable contract.
A schema change is a JOINT questions-file event with 0005c's owner — not
a unilateral edit, in either direction. The ratified division (Q9): the
frame stays per-book pure, `frame.best_prices()` is the ONE best-price
computation, and 0005c retires `publish/market.py`'s duplicate
best-price/de-vig selection in favour of frame outputs. One de-vig path,
one best-price path — or they drift.

**Ratified calls you should not silently revisit** (rulings in
`handoffs/reports/0005b-questions-answers.md`, Q8–Q14):

- Main AH line = closest-to-balanced (smallest |home − away| decimal),
  ties to smaller |line| then lower line. "Nearest the model's own line"
  was rejected **on principle**: a market-side attribute defined by the
  model leaks the two tracks into each other. Remember that reasoning
  before proposing any model-aware market attribute.
- Exchange mids: renormalized to sum to 1, never de-vigged, never in
  best price, shown under `market.books.{exchange}`. One rule for
  Polymarket and Kalshi alike.
- Bovada team totals are parsed (capture-where-offered); FanDuel's live
  on a tab we don't fetch — backlog, not v1.

**Two open items carried forward:**

- `polymarket_name` is 0/167 populated and the open-market test fixture
  is hand-built (disclosed). Polymarket lists no 2026-27 match-winner
  market for the five launch leagues yet. The first live listing is the
  trigger to populate aliases AND replace that fixture with a real
  capture.
- Kalshi soccer is gated — see §7.3 for the trigger and the four
  already-answered contract questions. Don't start early.

**The one that generalises past soccer:** the repo publishes
`backtest.json` measured against closing lines straight to the
public-read bucket. That is the exact surface where a licensed or
provider-restricted historical price could leak into a public artifact —
watch it on any backfill (§3, The Odds API historical carve-out, and the
Pinnacle rule).
