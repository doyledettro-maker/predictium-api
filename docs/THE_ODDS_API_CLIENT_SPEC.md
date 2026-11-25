# Spec: one shared The Odds API client in `predictium_odds`

Status: DRAFT for Portfolio review (2026-11-25), revised with Portfolio's
answers to the four open questions the same day. Spec only; no code lands
before this is approved. Owner: Books & Odds. Handover §7 item 7.

## Why

Doyle authorized The Odds API fleet-wide on 2026-11-21 for live and ongoing
ingestion as well as historical pulls, under the NFL repo's 2026-10-07
terms (handover §3). Without a shared client every sport would write its
own, and each one would carry the same four risks the terms exist to
control: a key that leaks into a log or a traceback, a job with no credit
cap, a rate-limit refusal read as an empty board, and a Pinnacle price
that escapes into a published object. This spec puts those controls in
one place, in the package every repo already pins, so a repo adopts the
source by repinning and configuring, never by writing a client.

## Non-goals

- No repo adopts it in this work. Each repo opts in by its own repin PR
  with its own review, as with every `predictium_odds` release.
- No scheduled job, cron entry or systemd unit. Scheduling belongs to the
  repo and the host (Claudia).
- No change to the keyless adapters (Bovada, FanDuel, ESPN, Kalshi,
  Pinnacle guest). The Odds API supplements them and replaces none.
- No publisher-side enforcement of the display boundary. That rule ("no
  standalone feed, no raw dump, no bulk historical file on the public
  bucket") binds publishers, and each repo's upload allowlist enforces it.
  This client only tags every quote so a publisher can tell.
- No unratified policy. The four questions the first draft left open were
  ruled by Portfolio on 2026-11-25 and are recorded under Decisions.

## Runtime host

The Linux production host the jobs are moving to. The client is
host-agnostic and reads only its environment. On that host the key and the
two account-floor settings (D3) reach the environment through a systemd
`EnvironmentFile=` owned by the job user with mode 0600 (Portfolio,
2026-11-25). Claudia places the values, and they are never printed. They
never come from a command-line argument (visible in `ps`), a crontab line,
or a file in any repository. Setting that up is Claudia's step on the host
and is outside this package.

## D1. Module and registration

- New module `predictium_odds/books/theoddsapi.py`, registered in
  `books.PROVIDERS` as `"theoddsapi"`.
- The `books/__init__.py` docstring still says "The Odds API is banned
  from ingestion org-wide; no adapter for it will be accepted." The same
  change rewrites it to cite handover §3 (2026-11-21).
- Standard library only (`urllib`, `json`, `hashlib`), like every other
  adapter. The package's `dependencies = []` stays empty.
- Release as 0.4.0: a new public adapter is a minor version.

## D2. The key

- Read from one environment variable, `THE_ODDS_API_KEY`, at call time,
  never at import. An absent or empty variable is a FAILED read with note
  `"no API key in environment"`, not an exception that kills the caller.
- The key travels only in the `apiKey` query parameter of the request the
  client builds itself. No other function receives it, returns it or
  stores it on an object.
- **Never in a log, an exception or a return value.** The URL contains the
  key, and urllib exceptions carry the URL (`HTTPError.url`,
  `HTTPError.filename`, and the text of some `URLError`s). So:
  - every network error is caught inside the client and replaced by a
    note built from the status code and a fixed phrase. When an exception
    must propagate, it is raised `from None`, so the original, which holds
    the URL, is not chained into the traceback;
  - the client never logs a URL. It logs the endpoint path and the
    parameters with `apiKey` removed;
  - provenance (D6) records the parameters with `apiKey` removed.
- A test injects a sentinel key and asserts that it appears nowhere: in
  the returned quotes, the report, the provenance, the credit log, captured
  log output, or the `str`, `repr` and formatted traceback of any
  exception, under every failure path (HTTP 401, 429, 500, timeout, DNS
  failure, invalid JSON).

## D3. Credit cap and credit logging

- Every call takes a `CreditBudget`, a required argument with no default.
  It carries `job` (a short name, for example `"nba_live_odds"`), `cap`
  (the most credits this job may spend in this run) and the running
  `spent`. A caller that wants no limit must say so in code, by passing a
  large cap, and that shows up in review.
- **Before** each request the client computes the request's cost from The
  Odds API's published cost rule for that endpoint (for odds, markets
  times regions; historical endpoints cost more per call). It refuses the
  call, without touching the network, when `spent + estimate > cap`. A
  refused call is a FAILED read with note `"credit cap: would spend N,
  M of C left"`. The cost table lives in one constant in the module, with
  the date it was checked against the provider's documentation. Verifying
  it is part of the implementation.
- **After** each request the client reads the credit headers
  (`x-requests-last`, `x-requests-used`, `x-requests-remaining`). It adds
  the actual `x-requests-last` to `spent` and returns all three with the
  read. When `x-requests-last` exceeds the estimate, the run's later
  calls use the actual figure, and the mismatch is logged as a warning so
  the cost table gets fixed.
- **A fleet-wide account floor, on top of every job's own cap**
  (Portfolio, 2026-11-25). No call may leave the account with fewer than
  20% of the monthly plan's credits remaining, so the fleet never spends
  more than 80% of a month's allowance. The floor is configuration, not
  code, and both values come from the host's environment:
  - `THE_ODDS_API_MONTHLY_CREDITS`: the plan's monthly credits. It is
    required. This repository is public, so the plan size is never written
    here, in a default or in a test.
  - `THE_ODDS_API_FLOOR_FRACTION`: the share that must stay unspent,
    default `0.20`, valid only in `[0, 1)`.

  How it works:
  - Before each request the client refuses the call when
    `remaining - estimate < floor_fraction * monthly_credits`, where
    `remaining` is the latest `x-requests-remaining` it has seen. The
    refusal is a FAILED read with note `"account floor: would leave R
    credits, floor F"`.
  - When the process has seen no `x-requests-remaining` yet, it reads one
    first from a request that costs nothing. The provider documents
    `GET /v4/sports` as free of quota cost; the implementation verifies
    that, and the test fixture records it. If that read fails, the
    billable call is refused (FAILED, `"account floor: remaining
    unknown"`). The floor fails closed.
  - A missing or invalid `THE_ODDS_API_MONTHLY_CREDITS`, or a floor
    fraction outside `[0, 1)`, is a FAILED read with note `"account floor
    not configured"`. Nothing is sent.
  - Every refusal by the floor logs one `WARNING` line, with no key and no
    URL:
    `theoddsapi account-floor job=<job> remaining=<r> estimate=<e> floor=<f>`.
    It also sets `report.ok = False`, so `health.evaluate` raises it. A
    floor that bites shows in the run's health summary.
- One log line per request, through the caller's logger (default
  `logging.getLogger("predictium_odds.theoddsapi")`), with no key and no
  URL:
  `theoddsapi job=<job> endpoint=<path> sport=<key> status=<captured|empty|failed> credits_last=<n> spent=<s>/<cap> remaining=<r>`.
  A job that discards the logger loses its credit record, so D7 also
  returns the figures in the report for the caller to persist.

## D4. Three read outcomes, never merged

The org rule: a CAPTURE, an EMPTY read (the venue answered with nothing)
and a FAILED read (it did not answer) are different things. A fetcher
returns None on failure and [] only on a real empty.

- The low-level reader returns an `OddsApiRead`:
  - `status`: `"captured"`, `"empty"` or `"failed"`;
  - `quotes`: a list, or `None` when failed;
  - `report`: a `SourceReport`;
  - `credits`: `{last, used, remaining}`, `None` when no response came back;
  - `provenance`: see D6.
- **FAILED**: no request was sent (no key, the job's cap, the account
  floor, or the floor not configured), or the
  provider did not answer usefully: a transport error, timeout, HTTP 429
  after retries, 5xx after retries, 401, 403, 404, 422, or an unparseable
  body. `report.ok` is False and the note names the cause without the URL.
- **EMPTY**: HTTP 200 with a valid body and no event the caller asked for
  (offseason, or nothing listed yet). `quotes` is `[]` and `report.ok` is
  True.
- **CAPTURED**: HTTP 200 with at least one quote.
- Retries follow the Kalshi adapter's rule, which is the org standard: on
  429, 500, 502, 503 and 504 only, backoff 2, 4, 8 and 16 seconds, and a
  `Retry-After` honoured only when it is above 0 and at most 60 seconds.
  The cap is checked again before each retry. A refusal that does not
  clear becomes FAILED, never EMPTY. A 401 is not retried: it means a bad
  or exhausted key and needs a person.
- The registry-shaped wrapper `fetch_game_lines(cfg, budget)` returns
  `(list[Quote], SourceReport)`, as every other adapter in `PROVIDERS`
  does. A FAILED read becomes `([], report)` with `report.ok == False`,
  which is how the package's health checks already tell a failure from an
  empty. Callers that need the strict None-versus-[] distinction use the
  low-level reader.

## D5. Quotes: one row per book, the shared shape

- Output is the package's existing `schema.Quote`, unchanged. Nothing new
  goes on the dataclass.
- One `Quote` per (event, bookmaker, market, side). Rows are never merged
  across bookmakers; a consensus is a derived view downstream.
- `book` is `"theoddsapi:<bookmaker_key>"` (for example
  `"theoddsapi:fanduel"`), never the bare `"fanduel"`. A FanDuel price
  through The Odds API is a different source from our direct FanDuel
  adapter. It has a different fetch time, different staleness and
  different terms, and per-book CLV and the health checks must be able to
  tell them apart. Portfolio ruled the tag 2026-11-25: it keeps the source
  visible in every row and cannot collide with our direct Bovada, FanDuel
  or Kalshi rows. Alias tables and coverage specs that name these books
  use the prefixed names.
- `extras` carries `{"source": "theoddsapi", "bookmaker": <key>,
  "bookmaker_title": <provider title>, "last_update": <provider ISO>,
  "license_class": <see below>}`. `source_ids` carries the provider's
  event id.
- `redistributable` and `license_class`:
  - a Pinnacle row (`bookmaker_key == "pinnacle"`) is always
    `redistributable=False` with `license_class="internal_only"`. The
    Pinnacle rule follows the bookmaker, not the transport (Portfolio,
    2026-11-21);
  - every other live row is `redistributable=True` with
    `license_class="display_in_model_payloads"`: it may appear inside our
    own payloads and pages, never as a standalone feed or dump;
  - every row from a historical endpoint is `redistributable=False` with
    `license_class="internal_only"`. Historical pulls are for training and
    backtests, and only live rows follow the display-in-payload rule
    (Portfolio, 2026-11-25).
- **Identity**: team names resolve through `cfg.resolve_team`, the
  committed alias tables, exactly as in every other adapter. There is no
  fuzzy matching. An unresolved event is skipped and counted in the report
  note. The same goes for bookmaker keys: only the keys in a committed set
  (`BOOKMAKERS`) become quotes, and any other key is counted and skipped.
- **Sign convention**: spread `line` uses betting convention (negative =
  favourite), checked against the provider's `point` field at this
  boundary only. A test covers a home favourite and a home underdog.
- **Pre-game only**: an event whose `commence_time` is at or before the
  fetch time is dropped and counted (`"k in-play dropped"`). The client
  never emits an in-play price.
- Prices: the request asks for American odds (`oddsFormat=american`), and
  a price that is not a finite non-zero integer is dropped and counted.
  `implied_prob` stays the package's vig-inclusive value. De-vig is
  downstream, as for every other source.

## D6. Provenance for historical pulls

Every read returns `provenance`:

- `endpoint` (path only);
- `params` (with `apiKey` removed);
- `fetched_at` (UTC ISO);
- `http_status`;
- `sha256` of the raw response body;
- `credits` (`last`, `used`, `remaining`).

The client never writes a file. A caller doing a historical pull passes
`keep_payload=True` to get the raw body back, and commits payload plus
provenance as its reproducible artifact, as golf's
`scripts/ingest_theoddsapi_golf.py` does today. The client does not decide
where artifacts live, and it never uploads anything.

## D7. Health

- Each read produces a standard `SourceReport` with `book="theoddsapi"`,
  the sport, the canonical market and `n_markets`.
- The report note adds a credit tail after the diagnostic, for example
  `"credits 6 (spent 18/200, remaining <n>)"`, so the credit record
  survives even when a caller drops the logger.
- Repos add `CoverageSpec` rows for The Odds API like any other source, so
  `health.evaluate` flags a silent outage.

## D8. Sport keys

`SportConfig.extras["theoddsapi_sport"]` carries the provider's sport key
(for example `americanfootball_nfl`, `basketball_nba`, `basketball_ncaab`,
`icehockey_nhl`). The repo supplies it, as it supplies `bovada_path` and
`kalshi_prefix` today. The client hard-codes no sport.

## Tests (written with the implementation, no network)

The urlopen stub serves recorded fixtures. No test calls the provider, and
no test needs the key.

1. The key never leaks (D2), under every failure path, through every
   output and the formatted traceback.
2. The cap refuses before the network. The stub asserts it was not called.
   Retries count against the cap.
3. The account floor works as specified. The cases:
   - it refuses a call that would leave fewer than 20% of a configured
     monthly figure, and lets through the call just above;
   - the floor fraction is read from the environment;
   - a missing or invalid `THE_ODDS_API_MONTHLY_CREDITS`, or an
     out-of-range fraction, refuses with nothing sent;
   - with `remaining` unknown, the zero-cost read happens first, and if it
     fails the billable call is refused;
   - every refusal logs the WARNING line and sets `report.ok = False`.

   Tests use a made-up plan size, never the real one.
4. Credit headers are parsed, added to `spent` and returned; the
   estimate-versus-actual mismatch warns.
5. A 429 retries with the documented backoff and then becomes FAILED, not
   EMPTY. The same holds for each retried 5xx. A 401 is not retried.
   `Retry-After` is honoured within 0 to 60 seconds and ignored outside it.
6. HTTP 200 with no events is EMPTY with `[]`. FAILED gives `None` from
   the low-level reader and `([], ok=False)` from the wrapper.
7. A Pinnacle row is `redistributable=False`, `internal_only`. Every
   historical row is `internal_only`.
8. `book` is `"theoddsapi:<key>"`. An unknown bookmaker key and an
   unresolved team are counted and skipped, never guessed.
9. An in-play event is dropped and counted.
10. The spread sign holds for a home favourite and a home underdog.
11. Provenance strips `apiKey`, and its `sha256` matches the payload.

## Definition of done

- This spec is approved by Portfolio.
- The implementation PR has: the module; the `PROVIDERS` entry; the
  rewritten registry docstring; the README section; a CHANGELOG 0.4.0
  entry; and all eleven tests passing with no network. The README names
  the three environment variables (`THE_ODDS_API_KEY`,
  `THE_ODDS_API_MONTHLY_CREDITS`, `THE_ODDS_API_FLOOR_FRACTION`) without
  any value.
- An independent reviewer APPROVEs, run by their own execution, with
  extra attention on D2 (key containment) and D3 (cap before network).
- Merged and tagged by SHA. Repos adopt it only through their own repin
  PRs.
- One live smoke read on the Linux host by Claudia, with a cap of a few
  credits, recorded as a committed artifact before any repo schedules it.
  That read confirms the account floor sees a real `x-requests-remaining`
  and that the EnvironmentFile supplies all three variables.

## Decisions (Portfolio, 2026-11-25)

- **(a) Book tag:** `"theoddsapi:<bookmaker>"` (D5).
- **(b) Historical pulls:** stay `internal_only`. They are for training and
  backtests; only live rows follow the display-in-payload rule (D5).
- **(c) Fleet-wide ceiling:** yes, as the account floor. No call may leave
  fewer than 20% of the monthly plan, the floor is configuration, and a
  refusal is logged (D3).
- **(d) Secret name:** `THE_ODDS_API_KEY`, provided on the Linux host
  through a 0600 systemd EnvironmentFile. Claudia places the value, and it
  is never printed (Runtime host, D2).
