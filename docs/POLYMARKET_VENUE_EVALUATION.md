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

## Q4. Liquidity at our actual size — ADEQUATE AT PROBE, THINS AT SCALE

Measured against the live schedule: `market-scope-2026-09-19.1`, bankroll
$20,000 committed, tier caps to-win Probe $200 / Core $600 / Scale $1,000,
per-bet risk cap $1,000, per-event $1,600.

Method: pulled real order books from `/v1/markets/{slug}/book`, walked the
offer side, and computed the contracts needed to win each tier target
(N = target / (1 − price)) and the volume-weighted slippage against the touch.
34 books with genuine two-sided quotes were measured.

Flat results, format: league / books measured / mean touch spread / fill
rate and mean slippage per tier.

MLB / 24 books / touch spread 1.01 cents /
  Probe $200: 24/24 fillable, 0.54 cents slippage
  Core $600: 24/24 fillable, 1.20 cents slippage
  Scale $1,000: 24/24 fillable, 2.06 cents slippage

MLS / 10 books / touch spread 1.51 cents /
  Probe $200: 10/10 fillable, 1.93 cents slippage
  Core $600: 10/10 fillable, 4.96 cents slippage
  Scale $1,000: 10/10 fillable, 12.42 cents slippage

Read that carefully. On MLB the venue is genuinely good: a Probe ticket costs
about half a cent of slippage on a one-cent spread. On MLS the same Probe
ticket costs nearly two cents, and a Scale ticket costs **12.4 cents**, which
would destroy any edge we think we have. The venue is not uniformly liquid
and must be gated per sport and per market, not adopted wholesale.

**Sampling limits, stated honestly.** The 70 markets I sampled resolved to
MLB and MLS only; **NFL and CFB depth is unmeasured** despite being the
largest open surfaces (240 and 83 open events). Open markets also skewed
overwhelmingly to futures — 496 of 500 sampled were `sportsMarketType:
futures` — so this measures futures depth, not game-line depth. Both gaps
are straightforward to close and should be closed before any funding
decision, because NFL and CFB game lines are where our actual volume is.

---

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

1. Capture BOTH, as distinct sources, never merged: `polymarket` (offshore,
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

On the evidence, **Polymarket US clears the bars that could have killed it**.
It is a CFTC-designated contract market, it lists every sport we model, its
resolution model is rulebook-based with named league sources rather than an
oracle, its fees are at parity with Kalshi with a better maker rebate, and
its books are genuinely tight on the liquid sports.

I am **not** recommending funding it yet, because four things are open and
three of them are cheap to close:

1. Page 3 of the Amended Order — the conditions — is unread (scanned image).
2. The CFTC-filed rulebook has not been read, which is where finality,
   dispute recourse and fund segregation are actually defined.
3. Custody and segregation of participant cash is undocumented publicly.
4. Tax reporting is undocumented; assume we self-report until told otherwise.

Plus one that is real work rather than a lookup: **NFL and CFB game-line
depth is unmeasured**, and that is where our volume would actually go. The
MLS numbers show what happens when we assume depth transfers across sports.

If Doyle wants to move, the cheapest order is: read the CFTC-filed rulebook,
ask support the custody and 1099 questions in writing, and let me measure
NFL and CFB game-line depth during a live slate. None of that requires an
account, a dollar, or a decision he cannot reverse.
