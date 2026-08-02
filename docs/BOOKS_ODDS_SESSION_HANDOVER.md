# Books & Odds session — role handover

_Last updated 2026-07-27. Written by the session that built the shared odds
layer, for whoever picks up the Books/Odds role next (any sport)._

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
`2da418a0712cf5c0054def6637df8dfc8d8a8b19`. Never float a branch. A real
`odds-v0.1.0` tag is still unminted — tag pushes are blocked from remote
sessions, so it needs a local `git tag -a odds-v0.1.0 <sha> && git push
origin odds-v0.1.0` (Claudia's task list).

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

- **The Odds API is banned.** Never propose, add, or reintroduce it. NBA was
  the last consumer; it's gone.
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

**Blocked / pending:**

- Mac Mini prod checkouts must `git pull` for tennis + MLB changes to reach
  the live tape (Claudia's task list).
- `odds-v0.1.0` tag unminted (see §2).
- Soccer Books/Odds work hasn't started — the session couldn't get repo
  access (§8).

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
3. **Kalshi spread/total for tennis** — the series exist
   (`KXATPGAMESPREAD`, `KXATPGAMETOTAL`) but list zero markets. When they
   open, the alignment machinery is already in place.
4. **NBA season-start checklist** (~late Oct) — load the staged plist, delete
   the legacy agent, eyeball the first slate.
5. **New sources** are a research-then-propose task, not a build task. The
   matrix has the current triage: DK/BetMGM/Caesars are bot-walled everywhere
   probed; Circa is licensed-vendor-only; **Betfair Exchange** is the one
   genuinely interesting unexplored candidate (documented API, deep
   soccer/tennis liquidity, but account + non-US geo — a business decision).

---

## 8. If you're the SOCCER Books/Odds session

The task is `specs/0005b-market-clients.md` in
`soccer_prediction_model_2026`, branched from
`origin/claude/spec-0005-publisher-core`, with ambiguities routed to
`handoffs/reports/0005b-questions.md`. Read that repo's `CLAUDE.md`,
`ROADMAP.md`, and `handoffs/multi_session_workflow.md` first — they bind you.

**Known blocker:** the repo may not be in your session's scope. `add_repo`
and `list_repos` returned `MCP error -32003: MCP tool call requires approval`,
and a settings.json allowlist did **not** clear it (the gate is server-side,
not Claude Code's permission layer). The durable fix is to add the repo to the
**environment's sources** in the Claude Code web UI so every new session has
it, then start fresh. Don't burn time on `add_repo`.

**Research already done for you — Kalshi soccer coverage** (live probe
2026-07-25, evidence for the questions file, *not* an implementation):

- 285 soccer series exist: EPL, UCL, Bundesliga 1/2, La Liga, Serie A,
  Ligue 1, MLS, CONMEBOL Libertadores/Sudamericana, plus derivatives.
- With domestic leagues in preseason, UCL qualifying was trading:
  `KXUCLGAME` 30/30 liquid across 10 fixtures, `KXUCLTOTAL` 60/60,
  `KXUCLSPREAD` 34/40, `KXUCLBTTS` 10/10. Example
  (`KXUCLGAME-26AUG05FENSTU`): Fenerbahce 68/70¢, Tie 18/20¢, Sturm Graz
  11/12¢.
- **The structural issue to raise before writing any adapter:** soccer's core
  market is **three-way (1X2 with a draw)**, and Kalshi models it as three
  binary contracts per event. A quote frame shaped around two-sided markets
  (the tennis/NFL moneyline pattern) cannot represent it. That is a
  contract-level question for the owning sessions, not something to resolve
  alone.
- Settlement semantics: Kalshi resolves on **90 minutes + stoppage,
  excluding extra time**. For knockout ties, "to advance" is a separate
  series. Books price 90-minute 1X2 by default, so they align — but don't
  assume it.
- Event tickers encode fixture date + both club codes, so the same committed
  alias discipline applies as everywhere else.
