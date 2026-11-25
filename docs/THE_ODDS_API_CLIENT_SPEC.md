# Spec: one shared The Odds API client in `predictium_odds`

Status: DRAFT for Portfolio review (2026-11-25). Revised the same day with
Portfolio's answers to the four open questions, and again for the
independent review of 422cd0c (REQUEST CHANGES: D2, D3, D4 and three
nits). Spec only; no code lands
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
  `"no API key in environment"`.
- The key travels only in the `apiKey` query parameter of the one request
  function that builds the URL. No other function receives it, returns it
  or stores it on an object, and it is never a default argument.
- **No exception ever leaves the client** (review of 422cd0c). Re-raising
  is not containment: `raise ... from None` only sets
  `__suppress_context__`, and `__context__` still holds the original
  `HTTPError` with its URL, which Sentry and any chain-walking formatter
  print. The URL is also a local variable in that frame, which
  `capture_locals`, Sentry and `pytest --showlocals` expose. So:
  - every public entry point (the low-level reader, `fetch_game_lines`,
    the quota read of D3) wraps its whole body in `except Exception`. Any
    exception becomes a FAILED read whose note is built AFTER the except
    block, from fixed phrases and, at most, the HTTP status code and the
    exception's class name. It never uses `str(e)`, `repr(e)`,
    `e.args`, `e.url`, `e.filename` or `e.reason`, and the exception
    object is not kept anywhere;
  - the try block covers both `urlopen` AND reading and decoding the body.
    Errors after `urlopen` returns are not `URLError`
    (`http.client.IncompleteRead`, `ConnectionResetError`,
    `socket.timeout`, `json.JSONDecodeError`, `UnicodeDecodeError`), and
    they still name the request in some Python versions;
  - redirects are disabled. The opener has no redirect handler, and a 3xx
    is FAILED with note `"unexpected redirect"`. A redirect would resend
    the URL, key included, to a host we did not choose;
  - the HTTP handler is built with `debuglevel=0` explicitly, so a global
    `http.client` debug setting cannot print the request line;
  - the client never logs a URL. It logs the endpoint path and the
    parameters with `apiKey` removed;
  - provenance (D6) records the parameters with `apiKey` removed.
- **The sentinel test** sets `THE_ODDS_API_KEY` to a unique sentinel and
  asserts it appears nowhere. It checks:
  - the returned quotes, report, credits and provenance (each `repr`ed);
  - every captured log record at every level;
  - captured stdout, stderr and `warnings`;
  - for any exception the test harness can observe, the full chain walked
    recursively through `__cause__` and `__context__`, rendered with
    `traceback.TracebackException(..., capture_locals=True)`.

  It runs under every failure path: HTTP 301, 401, 429, 500 and 503, a
  timeout, a DNS failure, a connection reset in the middle of the body,
  `IncompleteRead`, invalid JSON, and a body that is not UTF-8. Any
  exception reaching the test is itself a failure, because nothing may
  leave the client.

## D3. Credit cap and credit logging

What the controls are, stated plainly:
- **The job's cap is per run and in memory.** A `CreditBudget` lives in
  the process that created it. There is no shared budget file, so two
  processes each get their own cap.
- **The account floor is advisory.** It rests on the latest
  `x-requests-remaining` this process has seen, and another process may
  spend in between. It narrows the risk; it does not close it.

The rules:
- Every call takes a `CreditBudget`, a required argument with no default,
  carrying `job` (a short name, for example `"nba_live_odds"`), `cap` and
  the running `spent`.
  - `cap` must be a positive finite integer. Zero, a negative, a float, a
    bool, NaN or infinity raises `ValueError` when the budget is
    constructed, in the caller's own code, before any request. This is the
    one place the client raises, and it carries no key.
  - A caller that wants a very large cap writes it out, and that shows up
    in review.
- **Before** each request the client estimates the request's cost from
  The Odds API's published cost rule, held in one table in the module with
  the date it was checked against the provider's documentation:
  - **live odds**: markets times regions. A `bookmakers=` list counts as
    one region per started block of 10 bookmakers, so 1 to 10 bookmakers
    is one region and 11 to 20 is two;
  - **event odds**: an upper bound, the markets requested times the
    regions, even though the provider bills only the markets it returns;
  - **historical endpoints**: the same rule times the provider's
    historical multiplier (10 when this spec was written; the table holds
    the checked value);
  - **free endpoints** (the sports list, and any other the table marks
    free, verified): 0;
  - **any endpoint not in the table is refused** (FAILED, `"endpoint not
    in cost table"`). The client never guesses that a request is free.
- The call is refused, with nothing sent, when `spent + estimate > cap`
  (FAILED, `"credit cap: would spend N, M of C left"`).
- **Threads.** A `CreditBudget` shared across threads reserves the
  estimate under a lock before the request is sent and reconciles to the
  actual figure afterwards. Two threads can never both pass the cap check
  on the same remaining credits.
- **After** each request the client reads `x-requests-last`,
  `x-requests-used` and `x-requests-remaining`.
  - It adds the actual `x-requests-last` to `spent`, replacing the
    reservation.
  - If `x-requests-last` is missing or unparseable it adds the estimate,
    never 0, and logs a WARNING.
  - When the actual exceeds the estimate it logs a WARNING so the table
    gets fixed.
  - All three headers come back with the read.
- **Overshoot.** The estimate is an upper bound by construction, but the
  provider's billing is the truth. One call whose actual cost exceeds its
  estimate can take `spent` past `cap`. That call stands (the credits are
  spent), and every later call in the run is refused.
- **The fleet-wide account floor, on top of every job's own cap**
  (Portfolio, 2026-11-25). No call may leave the account with fewer than
  20% of the monthly plan's credits remaining, so the fleet never spends
  more than 80% of a month's allowance. The floor is configuration, not
  code, and both values come from the host's environment:
  - `THE_ODDS_API_MONTHLY_CREDITS`: the plan's monthly credits, a positive
    integer. It is required. This repository is public, so the plan size
    is never written here, in a default or in a test.
  - `THE_ODDS_API_FLOOR_FRACTION`: the share that must stay unspent,
    default `0.20`, valid only in `[0, 1)`.

  How it works:
  - Before each request the client refuses the call when
    `remaining - estimate < floor_fraction * monthly_credits`, where
    `remaining` is the latest `x-requests-remaining` this process has
    seen, less any reservations in flight (FAILED, `"account floor: would
    leave R credits, floor F"`).
  - When the process has seen no `x-requests-remaining` yet, it first
    makes a read that costs nothing (`GET /v4/sports`, documented as free;
    the implementation verifies this, and the fixture records it). If that
    read fails or carries no usable header, the billable call is refused
    (FAILED, `"account floor: remaining unknown"`). The floor fails closed.
  - A missing or invalid `THE_ODDS_API_MONTHLY_CREDITS`, or a fraction
    outside `[0, 1)`, refuses every call with nothing sent (FAILED,
    `"account floor not configured"`).
  - Every refusal by the floor logs one `WARNING` line, with no key and no
    URL: `theoddsapi account-floor job=<job> remaining=<r> estimate=<e>
    floor=<f>`. It also sets `report.ok = False`, so `health.evaluate`
    raises it.
- **Logging**, through the caller's logger (default
  `logging.getLogger("predictium_odds.theoddsapi")`), with no key and no
  URL:
  - one line per request: `theoddsapi job=<job> endpoint=<path>
    sport=<key> status=<captured|empty|failed> credits_last=<n>
    spent=<s>/<cap> remaining=<r>`;
  - one per-run total: `theoddsapi run job=<job> requests=<n> spent=<s>
    cap=<c> refused=<k> remaining=<r>`. `CreditBudget` is a context
    manager and writes this line on exit; `budget.summary()` returns it
    for callers that do not use `with`.

  A job that discards the logger loses this record, so D7 also returns the
  figures in the report for the caller to persist.

## D4. Three read outcomes, never merged

The org rule: a CAPTURE, an EMPTY read (the venue answered with nothing)
and a FAILED read (it did not answer) are different things. A fetcher
returns None on failure and [] only on a real empty.

- The low-level reader returns an `OddsApiRead`:
  - `status`: `"captured"`, `"empty"` or `"failed"`;
  - `quotes`: a list, or `None` when failed;
  - `report`: a `SourceReport`;
  - `credits`: `{last, used, remaining}`, `None` when no response came
    back;
  - `provenance`: see D6.
- **FAILED** covers two cases:
  - no request was sent: no key, the job's cap, the account floor, the
    floor not configured, or an endpoint missing from the cost table;
  - the provider did not answer usefully: a transport error, a timeout, a
    3xx, HTTP 429 or 5xx after retries, 401, 403, 404, 422, or a body that
    cannot be read or parsed.

  It also covers the review's case (422cd0c): **HTTP 200 that lists events
  but yields no quote, because every listed event or bookmaker was dropped
  as unresolved** (team names not in the alias table, bookmaker keys not
  in `BOOKMAKERS`). A broken alias table must never look like an
  offseason. The note names the counts, for example `"8 listed, 0
  resolved: 8 unresolved team"`.

  In every FAILED case `report.ok` is False and the note names the cause
  without the URL.
- **EMPTY**: HTTP 200 with a valid body and nothing priceable, for one of
  two reasons only:
  - no events are listed (offseason, or nothing posted yet);
  - every listed event had already started and was dropped as in-play.

  `quotes` is `[]` and `report.ok` is True, and the note says which.
- **CAPTURED**: HTTP 200 with at least one quote. Partial drops
  (unresolved, unknown bookmaker, in-play, bad price) are counted in the
  note.
- **Retries** follow the Kalshi adapter's rule, the org standard:
  - on 429, 500, 502, 503 and 504 only;
  - backoff 2, 4, 8 and 16 seconds;
  - a `Retry-After` honoured only when it is above 0 and at most 60
    seconds.

  The cap and the floor are checked again before each retry, and each
  retry reserves its own estimate. A refusal that does not clear becomes
  FAILED, never EMPTY. A 401 is not retried: it means a bad or exhausted
  key and needs a person.
- **The registry-shaped wrapper** `fetch_game_lines(cfg, budget)` returns
  `(list[Quote], SourceReport)`, as every other adapter in `PROVIDERS`
  does. A FAILED read becomes `([], report)` with `report.ok == False`,
  which is how the package's health checks already tell a failure from an
  empty. The README states this, so no caller reads the wrapper's `[]` as
  an empty board without checking `report.ok`. Callers that need the
  strict None-versus-[] distinction use the low-level reader.

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
  - a Pinnacle row is always
    `redistributable=False` with `license_class="internal_only"`. The
    Pinnacle rule follows the bookmaker, not the transport (Portfolio,
    2026-11-21). The match is exact: the provider's lowercase bookmaker key
    equal to `"pinnacle"`. No prefix, title or case-folded match, and any
    other spelling is an unknown bookmaker and is skipped. A test pins
    the key;
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

1. **The key never leaks** (D2): the sentinel test, across the full
   failure list, observed outputs, the recursive exception chain with
   `capture_locals=True`, stdout, stderr and warnings. No exception
   reaches the test.
2. **Redirects**: a 301 or 302 is FAILED, and the stub records that no
   second request was made.
3. **The cap refuses before the network.** The stub asserts it was not
   called. Retries reserve against the cap. Cap validation rejects 0, a
   negative, a float, a bool, NaN and infinity at construction.
4. **Cost estimation** covers each case:
   - markets times regions;
   - 10 and 11 bookmakers, which are one region and two;
   - an event-odds upper bound;
   - the historical multiplier;
   - a free endpoint costs 0;
   - an endpoint missing from the table is refused with nothing sent.
5. **Credit headers**: a missing or garbage `x-requests-last` adds the
   estimate and warns. Actual above the estimate warns. One overshooting
   call is kept and the next call is refused.
6. **Threads**: two threads sharing a budget whose cap allows only one
   call; exactly one request goes out.
7. **The account floor works as specified.** The cases:
   - it refuses a call that would leave fewer than 20% of a configured
     monthly figure, and lets through the call just above;
   - the floor fraction is read from the environment;
   - a missing or invalid `THE_ODDS_API_MONTHLY_CREDITS`, or an
     out-of-range fraction, refuses with nothing sent;
   - with `remaining` unknown, the zero-cost read happens first, and if it
     fails the billable call is refused;
   - every refusal logs the WARNING line and sets `report.ok = False`.

   Tests use a made-up plan size, never the real one.
8. **Retries**: a 429 retries with the documented backoff and then becomes
   FAILED, not EMPTY, and so does each retried 5xx. A 401 is not retried.
   `Retry-After` is honoured within 0 to 60 seconds and ignored outside it.
9. **The three outcomes** (D4):
   - no events listed is EMPTY with `[]`;
   - all listed events in-play is EMPTY;
   - all listed events unresolved is FAILED;
   - all bookmakers unknown is FAILED;
   - a partial drop is CAPTURED with the counts in the note;
   - FAILED gives `None` from the low-level reader and `([], ok=False)`
     from the wrapper.
10. **Licence**: a Pinnacle row (exact key `"pinnacle"`) is
    `redistributable=False` and `internal_only`. `"Pinnacle"` and
    `"pinnacle_eu"` are unknown and skipped. Every historical row is
    `internal_only`.
11. **Book tag and identity**: `book` is `"theoddsapi:<key>"`. An unknown
    bookmaker key and an unresolved team are counted and skipped, never
    guessed.
12. **Pre-game and sign**: an in-play event is dropped and counted. The
    spread sign holds for a home favourite and a home underdog.
13. **Provenance and logging**: provenance strips `apiKey` and its
    `sha256` matches the payload. The per-run total line is written on
    the budget's exit.

## Definition of done

- This spec is approved by Portfolio.
- The implementation PR has: the module; the `PROVIDERS` entry; the
  rewritten registry docstring; the README section; a CHANGELOG 0.4.0
  entry; and all thirteen tests passing with no network. The README names
  the three environment variables (`THE_ODDS_API_KEY`,
  `THE_ODDS_API_MONTHLY_CREDITS`, `THE_ODDS_API_FLOOR_FRACTION`) without
  any value. It also states that the wrapper returns `([], ok=False)` on
  a failed read, that the cap is per run and in memory, and that the
  account floor is advisory.
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
