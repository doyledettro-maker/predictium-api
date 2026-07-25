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
| `oddsmath` | conversions + de-vig: multiplicative, proportional, **Shin** (ML), **power** (n-way futures) — ported from the repos' canonical copies |
| `lines` | `best_line` (shopping), `consensus` (median line, decimal-mean price), `align_main_line` (THE correctness rule — see below) |
| `health` | `SourceReport` / `CoverageSpec` / `evaluate` → summary line + warnings + all-required-down signal |
| `books.bovada` | keyless public JSON; game lines + season win-total boards |
| `books.fanduel` | sbapi + public `_ak`; game lines (lobbies restructure silently — that's what health reports are for) |
| `books.kalshi` | keyless exchange market data; game quotes, strike ladders, `ladder_by_line` |
| `books.espn` | DraftKings lines via ESPN's public core API (`espn_dk`) |
| `books.pinnacle` | guest API, **internal-only** (`redistributable=False`) |

## Rules (org invariants — do not weaken)

1. **The Odds API is banned.** No adapter for it will be accepted.
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
  NFL/NBA/WNBA/MLB/NCAAF + `KXATPMATCH`/`KXWTAMATCH`.
- Thin contracts (spread > `MAX_SPREAD` = $0.10) are dropped and counted in
  the report note — treat thin exchanges like thin books.

## Tests

```
python -m pytest predictium_odds/tests -q
```

Fixture-based (no network). Live smoke: see the adapters' docstrings; all
five sources verified live 2026-07-23 (bovada 120 quotes, fanduel 192,
kalshi 369 win-total rungs / 32 teams, pinnacle 96, espn_dk 18-per-3-events).
