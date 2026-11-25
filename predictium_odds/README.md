# predictium-odds

Predictium's shared multi-book odds layer. One normalized `Quote`, one copy
of the odds math, adapters for every approved source, and a health-report
contract that makes silent single-source failure impossible.

Approved architecture (Doyle, 2026-07-23): shared package, installed from
this monorepo subdirectory with a **pinned tag** (never floating `main` — a
bad release must not be able to break six pipelines at once):

```
pip install "git+https://github.com/doyledettro-maker/predictium-api.git@odds-v0.1.0#subdirectory=predictium_odds"
```

If the layer is ever spun into a standalone product (the odds-service idea),
this directory is the seed — split it out with history at that point.

## Modules

| Module | What |
|---|---|
| `schema` | `Quote` — one priced side of one market from one source |
| `oddsmath` | conversions + de-vig: multiplicative, proportional, **Shin** (ML), **power** (n-way futures) — ported from the repos' canonical copies; fee-inclusive exchange EV (`kalshi_ev_at_ask`, rule 8) |
| `lines` | `best_line` (shopping), `consensus` (median line, decimal-mean price), `align_main_line` (THE correctness rule — see below), `price_at_strike` (a model's p at an offered strike, rule 8) |
| `health` | `SourceReport` / `CoverageSpec` / `evaluate` → summary line + warnings + all-required-down signal |
| `books.bovada` | keyless public JSON; game lines + season win-total boards |
| `books.fanduel` | sbapi + public `_ak`; game lines (lobbies restructure silently — that's what health reports are for) |
| `books.kalshi` | keyless exchange market data; game quotes, >= win-total ladders (`fetch_ladders`, `ladder_by_line`), every contract on any ladder (`fetch_ladder`, rule 8; `status` and `with_sizes` from 0.3.1), and one market by ticker (`fetch_market`, 0.3.1) |
| `books.espn` | DraftKings lines via ESPN's public core API (`espn_dk`) |
| `books.pinnacle` | guest API, **internal-only** (`redistributable=False`) |
| `books.theoddsapi` | The Odds API v4, the one **keyed** source: live, per-event and historical odds under a required `CreditBudget` (0.4.0) |

## Rules (org invariants — do not weaken)

1. **The Odds API goes through `books.theoddsapi` and nothing else.**
   Authorized fleet-wide for live, ongoing and historical reads (Doyle,
   2026-11-21; terms in the Books/Odds handover §3). Its prices appear only
   inside our own model payloads and pages, never as a standalone feed, a
   raw dump or a bulk historical file on the public bucket. Pinnacle rows
   and every historical row are internal-only. No repo writes its own
   client: the key handling and credit controls below live in one place.
2. **Main-line alignment before any price comparison.** Alt-line ladders
   (Kalshi strikes, book alt lines) are only comparable at the entry
   aligned to the consensus main line: `lines.align_main_line` /
   `books.kalshi.ladder_by_line`. A missing equivalent returns `None` and
   must be surfaced (warning/report) — **never** the nearest strike, which
   silently corrupts every EV/CLV number downstream. Kalshi "wins ≥ N"
   equals book "Over N − 0.5"; integer book lines have push semantics and
   no binary equivalent.
3. **Exchange quotes are never de-vigged** (`is_exchange=True` — the price
   is already a probability).
4. **`redistributable=False` quotes (Pinnacle) must never reach a published
   artifact or the public S3 bucket.** Internal consensus/fair value only.
5. **Every fetch returns a `SourceReport`.** Pipelines print the
   `evaluate()` summary line every run, print all warnings, and exit
   non-zero in-season when `all_required_down` (the WNBA capture-lines
   pattern, now shared).
6. Keys/secrets: none of these adapters need any. If a future source does,
   it goes through GitHub Actions secrets / env — never committed.
7. **A failed read is not an empty one.** Kalshi rate-limits by IP and the
   Mac mini runs every sport from one IP, so a burst of paged reads draws
   HTTP 429. `books.kalshi._get_page` retries the refused PAGE (not the
   series) with 2/4/8/16s backoff, honours `Retry-After`, and paces pages.
   An unclearable 429 still raises, deliberately: the caller turns it into
   `SourceReport(ok=False)`, which is what keeps "the venue did not answer"
   distinguishable from "the venue has no markets here". Never swallow a
   429 into `[]`. Tests: `tests/test_kalshi_retry.py`.
8. **Price the strike that is offered, from the model's own ladder, and
   settle at it** (Doyle, 2026-09-27). `books.kalshi.fetch_ladder` returns
   every contract with its strike normalised to an over line by strike TYPE:
   a `greater` half-point floor IS the line, a `greater_or_equal` integer N
   is over N − 0.5, spreads are team-oriented (negative = that team
   favoured). An unreadable strike raises `StrikeParseError`, never a guess.
   `lines.price_at_strike` returns the model's p at an offered strike from
   its published ladder, `None` off the grid (no interpolation; a continuous
   form, if the model publishes one, is used inside the grid only).
   `oddsmath.kalshi_ev_at_ask` prices it at the ask plus the SERIES' fee
   (`fee_multiplier` × 0.07 × P × (1 − P) for the quadratic fee types, read
   from `/series/{ticker}` at capture) and gives the to-win-1u risk at that
   fee-inclusive cost. An unknown fee type or missing multiplier returns None:
   no EV claim, never a default. Every contract carries its settlement rules
   verbatim. Evidence: `docs/KALSHI_FEE_EVIDENCE.md` in the repo root. Tests:
   `tests/test_ladders.py`.

## Integrating a repo (rollout pattern)

Each repo supplies a `SportConfig` — the only sport-specific code:

```python
from predictium_odds.books.base import SportConfig

CFG = SportConfig(
    sport="nfl",
    resolve_team=FULL_NAME_TO_ABBR.get,        # + city/nickname fallbacks
    event_key=lambda home, away, start_iso: f"{away}@{home}_{start_iso[:10]}",
    bovada_path="/football/nfl",
    fanduel_page_id="nfl",
    fanduel_state="nj",                         # unify on nj (MLB was on mi)
    espn_league="football/nfl",
    kalshi_prefix="KXNFL",                      # -> KXNFLGAME/SPREAD/TOTAL/WINS
)
```

Adapters normalize start times to UTC ISO before calling `event_key`, so one
key function covers every book. Kalshi game quotes are source-keyed
(`kalshi:{event_ticker}`) with resolved teams in `extras` — join them to
your schedule on (teams, date), the tennis `book_join` posture.

Roll out **behind a flag with the repo's current fetchers as fallback**:

```python
if os.environ.get("PREDICTIUM_ODDS_SHARED") == "1":
    quotes, reports = fetch_all_books(CFG)      # shared layer
else:
    ...                                          # existing per-repo code
```

Flip the flag per repo only after a side-by-side run shows quote parity;
delete the fallback a season later, not sooner.

## Kalshi notes

- Market data needs **no credentials** (auth is only for trading).
- API shape 2026-07: legacy integer-cent fields return null; prices are in
  `*_dollars` strings. Anything reading `yes_bid` as an int gets nulls.
- Series naming is uniform: `KX{SPORT}{GAME|SPREAD|TOTAL|WINS|MATCH}` —
  NFL/NBA/WNBA/MLB/NCAAF/NHL + `KXATPMATCH`/`KXWTAMATCH`.
- NHL (0.3.3, verified live 2026-11-17; `books.kalshi.NHL_SERIES`):
  `KXNHLGAME` (winner, full game including overtime and shootout),
  `KXNHLSPREAD` (puck line, team-oriented), `KXNHLTOTAL` (total goals) and
  `KXNHLTEAMTOTAL` (one team's goals). Read team totals with
  `fetch_ladder("KXNHLTEAMTOTAL", sport="nhl", team_total=True,
  team_names={code: name})`; the name table is required. Each rung then
  carries `team_code` and `subject`, and parsing raises unless:
  - the row has a strike (a structured row raises);
  - the ticker is under its event (starts with the event ticker and "-");
  - the ticker's code is one of the event's two teams (it starts or ends
    the event's matchup segment);
  - the rung equals the floor plus 0.5;
  - the subtitle reads "<name> over <line>" and that name is
    `team_names[code]`, matched exactly (case included).
  Without `team_total=True` (the default) every series parses as in 0.3.2,
  with one change from 0.3.3: "wins by" text on a non-spread contract now
  raises. `KXNHLWINS` and the player-prop
  series (`KXNHLGOAL`, `KXNHLPTS`, `KXNHLAST`, `KXNHLSAVES`,
  `KXNHLFIRSTGOAL`) are listed but had nothing open that day, so they are
  not verified yet.
- Thin contracts (spread > `MAX_SPREAD` = $0.10) are dropped and counted in
  the report note — treat thin exchanges like thin books.

## NHL (0.3.3, team-total checks 0.3.4)

```python
CFG = SportConfig(
    sport="nhl",
    resolve_team=NHL_NAME_TO_ABBR.get,         # the NHL repo's own table
    event_key=lambda home, away, start_iso: f"{away}@{home}_{start_iso[:10]}",
    bovada_path="/hockey/nhl",
    fanduel_page_id="nhl",
    kalshi_prefix="KXNHL",                     # -> KXNHLGAME/SPREAD/TOTAL/TEAMTOTAL
)
```

- FanDuel: the NHL lobby uses `MONEY_LINE`, `MATCH_HANDICAP_(2-WAY)` (Puck
  Line) and `TOTAL_POINTS_(OVER/UNDER)` (Total Goals), which the existing
  map already reads (verified live 2026-11-17). FanDuel lists no per-game
  team total for NHL. `TEAM_TOTAL_POINTS_SGP` on the lobby is a season
  standings-points future, and it is not mapped.
- Bovada: the parser already reads "Puck Line". `/hockey/nhl` is not yet
  verified live: Bovada returns nothing to the cloud session (NFL included),
  so the first run from the Mac mini must confirm it.
- Pinnacle: league 1456, internal only as always. Not yet verified live
  (the guest API answers 403 to the cloud session).
- Fees: `KXNHLGAME` is `quadratic_with_maker_fees` x 1, the other three are
  `quadratic` x 1. Both are handled by `oddsmath.kalshi_taker_fee`.

## The Odds API (0.4.0)

Spec: `docs/THE_ODDS_API_CLIENT_SPEC.md`. Standard library only.

Environment (names only; values never go in a repo, a log or a test):

| Variable | Required | What |
|---|---|---|
| `THE_ODDS_API_KEY` | yes | the key, read at call time; absent means a FAILED read |
| `THE_ODDS_API_MONTHLY_CREDITS` | yes, for any billable call | the plan size the account floor is measured against |
| `THE_ODDS_API_FLOOR_FRACTION` | no (default 0.20) | refuse a call that would leave less than this share of the plan; must be in [0, 1) |

```python
from predictium_odds.books import theoddsapi as toa

cfg.extras["theoddsapi_sport"] = "americanfootball_nfl"
with toa.CreditBudget(job="nfl-publish", cap=60) as budget:
    read = toa.read_odds(cfg, budget, markets=("h2h", "spreads", "totals"))
    if read.status == "failed":
        ...                  # read.report.note says why; read.quotes is None
```

- **Nothing raises except the budget.** `CreditBudget(job, cap)` raises
  `ValueError` when `cap` is not a positive int or `job` is empty. That is
  the one exception that leaves the client, and it is raised in your own
  code before any request. Every read returns an `OddsApiRead` instead of
  raising; its notes are built from fixed phrases, the HTTP status and an
  exception class name, never from the exception text (which carries the
  URL, and the URL carries the key). KeyboardInterrupt and SystemExit still
  propagate, with their traceback and context cleared.
- **Three outcomes.** `captured` (at least one quote), `empty` (no events
  listed, or every listed event in-play) and `failed` (no request sent, the
  provider did not answer usefully, or every listed event or bookmaker
  dropped as unresolved). A 429 or 5xx that survives the retries is failed,
  never empty.
- **The wrapper.** `fetch_game_lines(cfg, budget)` returns the registry
  shape `(quotes, SourceReport)`. A failed read comes back as
  `([], report)` with `report.ok` False, so check `report.ok` before
  reading an empty list as an empty board.
- **The cap is per run.** It lives in memory on the `CreditBudget`. Each
  attempt reserves its estimate before it is sent (retries included) and
  reconciles to `x-requests-last` afterwards. The call that overshoots
  stands; every later call on that budget is refused. Leaving the `with`
  block logs `theoddsapi run job=... requests=... spent=... cap=...
  refused=... remaining=...`; `budget.summary()` returns the same line.
- **The account floor is advisory.** It rests on the latest
  `x-requests-remaining` this process saw (probed from the free sports list
  when unknown), so another process can spend in between. When it bites,
  the read is failed and a WARNING `theoddsapi account-floor ...` is logged.
- **Licence on every row.** `book` is `theoddsapi:<bookmaker>` and
  `extras["license_class"]` is `display_in_model_payloads` or
  `internal_only`. Pinnacle (exact key `pinnacle`) and every historical row
  are `internal_only` with `redistributable=False`. Bookmakers outside the
  committed `BOOKMAKERS` set are skipped and counted.
- **The client writes nothing.** `read.provenance` holds the endpoint,
  params without the key, fetch time, HTTP status, body sha256 and credits;
  `keep_payload=True` also returns the raw body. Storing it is the caller's
  job, and a historical pull lands as a committed artifact.

## Tests

```
python -m pytest predictium_odds/tests -q
```

Fixture-based (no network). Live smoke: see the adapters' docstrings; all
five sources verified live 2026-07-23 (bovada 120 quotes, fanduel 192,
kalshi 369 win-total rungs / 32 teams, pinnacle 96, espn_dk 18-per-3-events).
