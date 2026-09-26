# Polymarket as a trading venue — evaluation

_2026-09-20, Books & Odds session, for Portfolio/Risk to relay to Doyle._

**Scope discipline: this is evaluation only.** No account was opened, no KYC
submitted, nothing funded, no wallet created, no API credential generated,
no test trade placed. Everything below comes from primary documents and from
unauthenticated public endpoints. Where an answer genuinely requires an
account, it is marked CANNOT CONFIRM WITHOUT ACCOUNT rather than guessed.

**Verification note.** My training cutoff is May 2026 and Polymarket's US
position moved after it. Every legal claim below is sourced to a primary
document with a date. I did not rely on memory, and I discarded several
secondary and SEO sources that surfaced in search.


> ## ⚠ SUPERSEDED IN PART — read this first (2026-09-26)
>
> **Doyle ruled on 2026-09-26: no standalone Polymarket collector on either
> venue** — no tape, no bulk pulls, no scheduled capture, in any repo. Only
> execution-incidental data (quotes and fills our own executor sees while
> trading Polymarket US through its official API) may be stored, internally,
> and only once we trade there. Permission requests are drafted in
> `POLYMARKET_DATA_PERMISSION_REQUESTS.md`.
>
> Three things in this document were wrong, all mine:
>
> 1. **Q7 recommended capturing BOTH venues.** Both venues' terms bar the
>    collection itself, not just publication. Offshore §4.2 bars scraping
>    tools and needs written consent even for manual copying; Polymarket US
>    §5 licenses data for "personal, non-commercial use" and bars "scraping,
>    bulk downloads" unless expressly licensed. I recommended capture without
>    having read either terms document.
> 2. **I relied on golf's ratified "CLEAR" row for the offshore venue** and,
>    asked for an independent check, reported "I read the row... I have no
>    quarrel with it." I read the row, not the file it cites. That file is a
>    1,585-byte navigation shell containing no terms text. The CFB session
>    read the real 59,371-byte terms and found them prohibitive.
> 3. **Q4's NFL depth figures were a lucky sub-sample.** I reported a
>    0.75-cent touch and 0.80 cents of slippage at Probe. On NFL Sunday,
>    2026-09-20, properly paginated and live, the numbers were a 7.95-cent
>    touch and 4.51 cents at Probe. I corrected this in chat to Doyle that
>    day and **did not commit it here**, so the wrong number propagated —
>    it is the premise of the ruling's "NFL is the first candidate". Q4 and
>    the Recommendation below are now corrected.

---

## The finding that reframes everything: there are two Polymarkets

This distinction runs through every question and is the single most
important output of this evaluation.

**Polymarket (offshore)** — `polymarket.com`, with `gamma-api.polymarket.com`
and `clob.polymarket.com`. Its own site states: *"Trading is blocked in the
United States on polymarket.com."* This is the venue with the UMA optimistic
oracle, token-holder dispute voting, and the contested-resolution history.

**Polymarket US** — `polymarket.us`, operated by **QCX LLC d/b/a Polymarket
US**, a CFTC-designated contract market. Different venue, different
settlement model, different legal posture, different API.

They are not interchangeable, and almost every published claim about
"Polymarket" — including most of what is in my training data and most of
what a search returns — is about the offshore one.

---

## Q1. Access and legal status — NOT FATAL

**Primary source:** CFTC *Amended Order of Designation, In the Matter of the
Petition of QCX LLC d/b/a Polymarket US to Amend Its Order of Designation*,
retrieved 2026-09-20 from cftc.gov/media/12806.

Verbatim from that order:

- "on July 9, 2025, the Commodity Futures Trading Commission ... issued an
  order ... designating QCX LLC (the 'Exchange,' which now operates under
  the business name Polymarket US) as a contract market"
- The original order barred FCM intermediation: the Exchange "may not permit
  any futures commission merchant to intermediate any transactions or carry
  accounts for customers ... unless this Order of Designation has been
  amended"
- The Exchange petitioned between 2025-08-14 and 2025-11-24 to remove that
  provision; the Commission found it "has demonstrated its ability to comply
  with the core principles" and ordered the provision "vacated and superseded"

So: Polymarket US is a **CFTC-designated contract market since 2025-07-09**,
and the amendment **adds** intermediated access through FCMs on top of the
direct access the original designation already allowed. The amendment is not
what makes US access legal; the July 2025 designation did that.

**Sports contracts specifically are listed and operational.** The public
`/v1/sports` endpoint returns 265 sport slugs, and every sport we model is
present with `isOperational: true`, including nfl, nba, mlb, wnba, cfb, atp,
wta, epl, and the golf majors. NFL Super Bowl and division futures are live
on the site right now. This is not a venue where sports are theoretical.

**CANNOT CONFIRM.** Page 3 of the Amended Order — the operative "terms and
conditions" following the ordering clause — is a scanned image with no
extractable text, and I could not OCR it in this environment. The conditions
attached to the amendment are therefore unread. Someone should read that page
before any funding decision. It is three pages; the unread part is short.

**Also unverified: the state-law overlay.** CFTC designation is federal.
Several states have taken positions on sports event contracts at other
venues. I did not find a primary source resolving Doyle's state position and
I am not going to infer one. Flagging it as open rather than answering it.

**Entity structure.** The order designates QCX LLC. Whether Doyle would
trade as an individual or through an entity is a tax and liability question
below his risk tolerance to decide, not something the order constrains.

Verdict: **not fatal, proceed to Q2.**

---

## Q2. Resolution risk — MATERIALLY LOWER THAN THE PREMISE ASSUMED

Portfolio/Risk asked for this as a first-class risk on the grounds that
"Polymarket resolves through an oracle with a dispute process, which is a
structurally different settlement guarantee from Kalshi's." **That is true
of the offshore venue and not true of Polymarket US.**

Primary source: docs.polymarket.us/learn/markets/market-resolution and
/contract-settlement, retrieved 2026-09-20. Verbatim:

- "Once the outcome becomes publicly known, **the Exchange** determines the
  result using publicly verifiable information."
- "Markets resolve using the **specific sources listed in the rules**. These
  sources provide the authoritative outcome. **Unlisted sources have no
  effect** on market resolution."
- Sports: "Sports markets resolve using results published by the **governing
  league or competition organizer**."
- "All resolutions are final in accordance with the Polymarket US Exchange
  Rulebook."
- Settlement is processed by **Polymarket Clearing**; winners settle at
  $1.00, losers at $0.00.

The public API corroborates it: `/v1/sports` publishes a named resolution
source per major sport — nba.com, nfl.com, mlb.com, ncaa.com, nhl.com,
mlssoccer.com, ufc.com — and `automaticResolution` is false for all of ours,
meaning rulebook-based determination rather than an automated feed.

There is **no UMA oracle, no token-holder vote, and no public dispute
market** on the US venue. The settlement guarantee is an exchange rulebook
under CFTC DCM obligations, which is the same *class* of guarantee as
Kalshi, not a different one.

**The residual risk is different from what was feared, and it is real.**
"All resolutions are final" appears in the participant documentation with no
participant-facing dispute or appeal process described anywhere I could find.
Finality without described recourse means that if the Exchange resolves
against what we believe the factual outcome was, our remedy is not a dispute
window — it is a complaint through CFTC/self-regulatory channels after the
fact, with the position already settled. Kalshi carries the same structural
property, so this is not a reason to prefer one over the other; it is a
reason not to treat either as risk-free.

**CANNOT CONFIRM WITHOUT THE RULEBOOK.** The finality language points to the
Polymarket US Exchange Rulebook, hosted on `polymarketexchange.com`. I did
not treat that domain as primary because I could not establish from CFTC
sources that it is the Exchange's own property rather than a lookalike, and
confusing the two is exactly the failure mode this evaluation exists to
avoid. The rulebook is separately on file with the CFTC — a rule filing
"Amendment to the Polymarket US Rulebook" was submitted 2026-03-20 and
certified 2026-04-03 (CFTC filing 60073). **Read the CFTC-filed copy, not
the website copy.**

**The contested-resolution cases Portfolio/Risk asked for** belong to the
offshore venue and its UMA oracle. I deliberately did not import them as
evidence about the US venue, because they are evidence about a different
settlement mechanism. If Doyle wants that history documented anyway — as
reputational or counterparty context about the parent — say so and I will do
it as a separate piece of work, clearly labelled as being about the offshore
venue.

**Proposed exposure rule if we ever trade there.** Cap per-event exposure at
the same level as Kalshi rather than treating it as a lower-trust venue,
because the settlement class is the same — but add one Polymarket-specific
condition: do not take a position where our edge depends on a resolution
reading that differs from the plain reading of the named league source.
Concretely, if the market's listed source is nfl.com and our model's view
depends on a statistic nfl.com does not publish in the form the rule
references, that is not an edge, it is an unpriced resolution bet. This is
cheap to apply and it targets the actual failure mode.

---

## Q3. Cost — COMPETITIVE, essentially at parity with Kalshi

Primary source: docs.polymarket.us/fees, "Effective exchange-wide from
12 AM ET, Thursday September 17, 2026" — three days before this evaluation.

Fee formula, verbatim: `Fee = Θ × C × p × (1 - p)` where C is contracts, p
is trade price, Θ is the fee coefficient.

- Taker Θ = **0.0695**, maximum $1.74 per 100 contracts at p = $0.50
- Maker Θ = **−0.0125**, i.e. a **rebate of $0.31 per 100 contracts** at
  p = $0.50, applied at the point of trade
- Taker volume rebates: 10% above $250k prior-month notional, 25% above $1m,
  50% above $10m. Not relevant at a $20k bankroll.

This is the *same functional form* as Kalshi's fee, with Θ = 0.0695 against
Kalshi's 0.07 — marginally cheaper on the taker side, and Polymarket US pays
a maker rebate where Kalshi does not. At our price floor of 0.35 the taker
fee is 0.0695 × 0.35 × 0.65 = **1.58 cents per contract**; at 0.50 it is
1.74 cents.

The live API exposes this per market as `feeCoefficient` (observed 0.0695),
so it can be read at quote time rather than hard-coded — which is the right
way to bake it into EV, and better than what we do for Kalshi today.

**Ramp costs.** Deposits by debit card, ACH (via Aeropay) or wire.
Withdrawals are documented as **free**. No gas or network fee appears
anywhere in the US venue's documentation, which follows from it being a
conventional cleared exchange rather than an on-chain settlement venue — the
offshore venue's gas costs do not apply here.

Net: cost is **not** a reason to avoid this venue. It is at parity with
Kalshi, and the maker rebate is a genuine improvement if we ever post rather
than take.

---

## Q4. Liquidity at our actual size — THE TOUCH IS TIGHT, THE BOOK IS THIN

**Revised 2026-09-20, second pass.** My first pass measured futures depth
and read encouragingly. Pre-match game lines — what we would actually trade
— look materially worse, and that distinction is the whole answer.

### Correcting two errors in the first pass

**Error one: I concluded Polymarket US was a futures venue. It is not.**
The `/v1/events` endpoint caps at 500 per page and I drew conclusions from
page one twice. Paginating properly with `offset`:

- **4,096 distinct open events**
- **60,357 non-futures markets**, of which **44,249 have a future start**
- By league: NFL 20,089, CFB 15,365, MLB 1,776, WTA 428, Serie A 395,
  La Liga 395, EPL 318
- Types include full-game spread (4,097 pre-match), full-game total (1,706),
  team points total, first-half and second-half spreads and totals, exact
  margin, player touchdowns, total first downs, rush yards, and soccer
  full-time winner (1,101)

So the breadth is real and large, and heavily NFL and CFB.

**Error two: futures depth does not predict game-line depth.** It is better.

### Pre-match game-line depth, measured

Method: paginated all open events, filtered to non-futures market types with
a future game start, pulled real order books, **excluded settled and
near-settled markets** (best ask ≥ 0.98 or ≤ 0.02 — the 0.999-bid/1.00-ask
trap tennis flagged, which is real here too: 10 of 99 sampled books were
settled, 6 CFB and 4 NFL). Walked the offer side for each tier target and
measured volume-weighted slippage against the touch.

Flat lines, format: league / tradeable books / mean touch spread / fill rate
and slippage per tier.

NFL / 36 books / touch spread 0.75 cents / **SUPERSEDED — see below**
  Probe $200: 36/36 fillable, 0.80 cents slippage
  Core $600: 36/36 fillable, 6.14 cents slippage
  Scale $1,000: 36/36 fillable, 10.42 cents slippage

CFB / 34 books / touch spread 23.04 cents /
  Probe $200: 27/34 fillable (79%), 29.75 cents slippage
  Core $600: 27/34 fillable, 44.07 cents slippage
  Scale $1,000: 27/34 fillable, 46.96 cents slippage

MLB / 6 books / touch spread 0.67 cents /
  Probe $200: 6/6 fillable, 7.68 cents slippage
  Core $600: 6/6 fillable, 14.37 cents slippage
  Scale $1,000: 6/6 fillable, 19.26 cents slippage

NCAA women's / 9 books / touch spread 83.11 cents / 1 of 9 fillable at Probe
USL Championship / 4 books / touch spread 55.75 cents / 2 of 4 at Core

### What this actually says

**A tight touch is not depth, and on this venue the gap between them is
enormous.** NFL quotes 0.75 cents at the touch — better than anything else
we carry — and then costs 6.14 cents to fill a Core ticket and 10.42 to fill
a Scale one. The top of book is excellent and there is very little behind it.

The one genuinely usable configuration today is **NFL at Probe size**: 100%
fillable at 0.80 cents of slippage, which is competitive with Novig and
better than any sportsbook. That is a real finding and it is narrow.

Everything else fails on our numbers. CFB has a 23-cent touch spread despite
15,365 pre-match markets, so breadth there is listing breadth, not
tradeable breadth. MLB's touch is the tightest measured at 0.67 cents and
still costs 7.68 cents at Probe, meaning almost nothing rests behind the
quote. Anything above Probe size, in any sport, pays more slippage than our
edge.

**Recommended gate if we ever trade here:** NFL full-game spreads and totals
at Probe size only, with a per-order slippage ceiling enforced at execution
(walk the book before sending, refuse if VWAP exceeds touch by more than a
stated bound). Do not enable by sport on breadth; enable by measured depth
and re-measure, because these numbers will move as the venue grows.

**Still unmeasured:** in-season NBA and NHL (season not started at probe
time), and whether NFL depth improves closer to kickoff. Both are worth a
second pass during a live Sunday slate before any funding decision.

---

### CORRECTION — live NFL Sunday, 2026-09-20, 2:50pm CT

The NFL figures above came from a 36-book sample drawn from one unpaginated
page. Re-measured live mid-slate, from the full paginated pool of 2,444
pre-match NFL core markets, same method, same tier caps:

Polymarket US NFL pre-match / 43 tradeable books / touch 7.95 cents /
  Probe $200: 42/43 fillable, 4.51 cents slippage
  Core $600: 42/43 fillable, 13.33 cents
  Scale $1,000: 42/43 fillable, 18.19 cents

Polymarket US NFL in-progress / 15 books / touch 4.10 cents /
  Probe $200: 14/15, 18.15 cents — the quote narrows live while size vanishes

Kalshi, same slate, for comparison:

KXNFLGAME (moneyline) / 68.5M contracts traded, 43.1M open interest / touch 2.60 cents /
  Probe 0.77 cents, Core 1.74, Scale 2.90 — all 100% fillable
KXNFLTOTAL / touch 8.47 cents / Probe 1.72, Core 3.07, Scale 5.74
KXNFLSPREAD / touch 10.40 cents / Probe 3.37, Core 8.51, Scale 13.27

**Polymarket US loses to Kalshi at every tier on comparable markets, and
Kalshi moneylines are in a different class entirely.** The "NFL at Probe
size is genuinely competitive" conclusion below does not survive the
corrected sample.

## Q5. Funding and custody — VENUE FLOAT, AND SLOW TO CONVERT BACK

Primary source: docs.polymarket.us/learn/deposits/*, retrieved 2026-09-20.

- Deposit methods: debit card, bank transfer (ACH, via Aeropay), wire.
- Clearing time before funds are withdrawable: debit card 3–4 business days,
  ACH 3–4 business days, wire 1 business day. You **can trade while funds
  are "in flight"** but cannot withdraw them.
- Withdrawals are free. Typical arrival: debit card 3–4 business days, ACH
  3–4 business days, wire 1 business day.
- **Withdrawals must return to the original funding source**, and are
  processed **first-in-first-out** across deposit methods, stated as an AML
  requirement. Deposit $100 by card then $200 by ACH, and the first $100 out
  goes back to the card.

For our bankroll discipline the answer is clear: a Polymarket US balance is
**venue float, not committed bankroll that can be recalled quickly**. Round
trip is realistically a week on ACH and about two business days on wire. The
FIFO-to-original-source rule also means the float is not fungible on the way
out — it returns along the path it came in, which matters if Doyle funds
from more than one account.

**CANNOT CONFIRM WITHOUT ACCOUNT.** Who custodies participant cash, whether
customer funds are segregated under CFTC rules, and whether any FDIC
pass-through applies are not stated in the participant documentation I could
reach. "Polymarket Clearing" is named as the settlement entity. This is a
real gap and it is exactly the kind of question that should be answered
before money moves. It is likely answerable from the CFTC-filed rulebook
without opening anything.

**What happens to open positions if access changes** is likewise not
documented publicly. Given the venue's own history of US access changing,
this is not a hypothetical question and I would want it answered in writing.

---

## Q6. Tax and reporting — DOCUMENTATION GAP

I searched the complete documentation index (`docs.polymarket.us/llms.txt`,
48,925 bytes, every page listed) for tax, 1099, and statement references.

**There is no tax documentation.** No 1099 page, no tax-reporting page, no
statement of what forms a US participant receives. The only reporting
surfaces are operational: a Reporting Data page, a Report API, and CSV
exports of orders, trades and executions.

Kalshi issues a 1099-B. I could find no equivalent commitment from
Polymarket US. Two possibilities — either it exists and is undocumented, or
it does not — and I am not going to guess which.

**Practical consequence:** assume we run our own records. The CSV export
endpoints (`/api-reference/report/download-executions-csv`, `download-trades-csv`,
`download-orders-csv`) are the raw material, and our own settled-pick ledger
would have to be the authority, reconciled against those exports. That is
work we largely already do for Kalshi and Novig, so it is not disqualifying
— but it should be scoped as real work, not assumed away.

This question is answerable by asking Polymarket US support directly without
opening an account, and that is the cheapest way to close it.

---

## Q7. Capture without an account — YES, BUT WE ARE ABOUT TO CAPTURE THE
## WRONG VENUE

**This is the item Portfolio/Risk asked me to surface before seven sessions
build against it.**

Both answers are yes, and they are different venues:

- Offshore: `clob.polymarket.com/markets` and
  `gamma-api.polymarket.com/markets` both return 200 anonymously. This is
  what soccer's existing client uses (`soccer_model/data/books/polymarket.py`,
  `BASE_URL = "https://gamma-api.polymarket.com"`).
- US DCM: `https://gateway.polymarket.us/v1/*` is public and keyless —
  markets, events, series, sports, search, full order books via
  `/v1/markets/{slug}/book`, BBO, settlement prices and price history. I
  verified these anonymously from cloud egress.

I nearly reported a blocker here. `api.polymarket.us` returns 401 "Missing
required API key headers" on **every** path including `/health`, and on that
evidence alone the honest conclusion would have been "the US venue requires
an account." It does not — the public reads live on a **different host**
(`gateway.` not `api.`) that is documented but easy to miss. Worth recording
because the next person will hit the same 401.

**The substantive problem is which venue the sport sessions capture.** The
directive tells them to port soccer's Polymarket client, which points at the
offshore venue. If they do that, the org will standardize on capturing a
tape from a venue **Doyle cannot legally trade**, and any EV computed
against those prices is uncollectable.

That is not an argument against capturing offshore Polymarket — it is a deep,
liquid market and a legitimate fair-value reference. It is an argument for
labelling it correctly and never letting it price a bet. Recommendation:

1. ~~Capture BOTH~~ **WITHDRAWN 2026-09-26 — both venues' terms bar collection; see banner.** Originally: capture both, as distinct sources, never merged: `polymarket` (offshore,
   reference only, `tradeable=False`) and `polymarket_us` (the DCM,
   potentially tradeable).
2. Write a new shared adapter for `gateway.polymarket.us/v1` rather than
   porting the gamma client for it. The schemas are different; the gamma
   client will not fit.
3. Tell the seven sessions now, before they build. If they port the existing
   client unchanged they will each independently produce a source labelled
   "polymarket" that silently means the offshore venue.

---

## Recommendation

**Revised 2026-09-20 after measuring pre-match depth.** The verdict is
narrower than the first pass suggested, and the narrowing is the finding.

On the evidence, **Polymarket US clears the bars that could have killed it**.
It is a CFTC-designated contract market, it lists every sport we model, its
resolution model is rulebook-based with named league sources rather than an
oracle, and its fees are at parity with Kalshi with a better maker rebate.

**Depth, not legality, decides this — and on the corrected live-Sunday
sample, depth does not favour Polymarket US anywhere.** NFL pre-match costs
4.51 cents at Probe and 18.19 at Scale against Kalshi's 0.77 and 2.90 on the
same slate. CFB carries 15,365 pre-match markets at a 23-cent touch, which is
listing breadth rather than tradeable breadth. There is no sport and no tier
where Polymarket US beat Kalshi in anything I measured.

I am **not** recommending funding it, for two different kinds of reason.

Cheap lookups still open:

1. Page 3 of the Amended Order, the conditions, is unread (scanned image).
2. The CFTC-filed rulebook has not been read, which is where finality,
   dispute recourse and fund segregation are actually defined.
3. Custody and segregation of participant cash is undocumented publicly.
4. Tax reporting is undocumented; assume we self-report until told otherwise.
5. Polymarket US is **not covered by the ratified Polymarket terms row** —
   that row is about the offshore Gamma/CLOB surface. The US DCM is a
   different venue and needs its own clearance under the new precondition.

And one that is judgement rather than lookup: **a venue that supports one
sport at one tier is not worth a funding decision yet.** The right move is
to re-measure during a live Sunday NFL slate, see whether depth thickens
near kickoff, and check NBA and NHL once their seasons start. If NFL depth
holds at Core size in-season this becomes interesting; if it does not, we
have learned that cheaply with nothing at risk.

~~Meanwhile the capture case is strong and separable from the trading case.~~
**Withdrawn 2026-09-26.** Both venues' terms bar the collection itself. Under
Doyle's ruling there is no capture case until a licence or written consent is
granted; see `POLYMARKET_DATA_PERMISSION_REQUESTS.md`.
