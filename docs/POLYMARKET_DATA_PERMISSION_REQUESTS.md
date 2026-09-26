# Polymarket data permission requests — drafts for Doyle

_2026-09-26, Books & Odds session. Drafted under Doyle's ruling of the same
day (option 1, item c). **Nothing has been sent and no one at Polymarket has
been contacted.** Doyle sends these himself, or edits or discards them._

## The ruling these sit under

Doyle, 2026-09-26, relayed by Portfolio/Risk:

- **(a) No standalone Polymarket collector on either venue.** No tape, no
  bulk pulls, no scheduled capture, in any repo.
- **(b) Execution-incidental data is allowed.** Quotes and fills our own
  executor sees while trading on Polymarket US through its official trading
  API may be stored internally, never public. Applies only once we trade
  there. Opening the account is Doyle's step; the executor is Claudia's.
- **(c) Request permission in parallel.** These two drafts.

## What the terms actually say, read at source

Both documents were captured in full on golf branch
`claude/golf-polymarket-terms` @ `e674812` and read there by the CFB session
on 2026-09-26. I re-read both files myself rather than relaying the summary.

**Offshore — polymarket.com, operated by Adventure One QSS Inc.** Terms of
Use effective 2026-08-11, 59,371 bytes.

- Byte 27,061, §4.2: no "data mining tools, robots, crawlers, or similar data
  gathering and extraction tools to scrape or otherwise remove data from the
  Site, any other Interface, or Features."
- Byte 27,239: no "manual process to monitor or copy any of the material on
  the Site ... without our prior written consent."
- Byte 28,846: accessing "the Data" — defined to cover access "directly or
  through an API ... whether in raw, derived, aggregated, or anonymized form"
  — is barred to a "Capital Market Client", defined as "a non-retail,
  professional entity that engages in capital markets activities (e.g.,
  brokerage, market making, proprietary trading, index calculation, or ETF
  issuance), including but not limited to ... proprietary trading firms ...
  financial technology companies ..." — "unless otherwise agreed to in
  writing by us."
- Byte 1,852: the terms bind anyone "INTERACTING WITH OR USING THE SITE ...
  (INCLUDING BY CONNECTING VIA AN API ...)". The public gamma and clob hosts
  are inside scope.
- US persons cannot trade this venue at all.

**Polymarket US — QCX LLC, a CFTC-designated contract market.** Terms of Use
effective 2025-09-25, 26,774 bytes.

- Byte 3,115, §5: market data is "licensed to you for personal,
  non-commercial use in connection with your trading ... Redistribution,
  resale, scraping, bulk downloads, and commercialization of derived works
  are prohibited unless expressly licensed."
- Byte 4,230, §7(c): no "bots or automation except through authorized APIs."
  That permits automated **trading** through the API; it does not license an
  organisation's bulk market-data tape, which §5 still governs.
- The same section notes "the official DCM Rulebook/contract terms control."

## The one thing these letters must not get wrong

The ruling frames the ask as "internal research and model evaluation only,
nothing redistributed or published." That is **true about their data**: no
Polymarket price would ever reach our site, our bucket, or anyone else.

It is **not the whole truth about us**, and both sets of terms turn on
exactly the part it leaves out:

- Polymarket US §5 licenses data for "personal, **non-commercial** use."
- The offshore clause bars a "**professional entity**" including
  "**financial technology companies**" and "**proprietary trading firms**."

Predictium sells subscriptions to model outputs. Polymarket data would be used
to calibrate and evaluate models whose outputs are sold. A reasonable reader
at either company could call that commercial use, or call us a fintech or a
proprietary trading operation, or both.

**So the drafts say it plainly.** A permission granted on a description that
omitted the commercial product would be void the day they learned the rest,
and it would leave us worse off than not asking — we would have a letter we
could not rely on, and a counterparty who had reason to feel misled. Stating
it up front costs, at most, a no or a price. Hiding it costs the permission.

I have made the same correction to the Novig draft, which had the same gap.
See `NOVIG_DATA_ACCESS_REQUEST.md` §4 — it described us as "a private sports
modelling operation" without saying the outputs are sold.

---

## Draft 1 — Polymarket US (QCX LLC): market-data licence

```text
Subject: Market data licence enquiry for internal model evaluation

Hello,

I'd like to ask about licensing Polymarket US market data, and I want to
describe the use accurately so that whatever you tell me is something I can
rely on.

Who I am: I run Predictium, a sports analytics business. We build
probabilistic models for sports and sell access to their outputs by
subscription. I also trade on prediction markets with my own capital.

What I'm asking for: a licence to read Polymarket US market data (events,
markets, prices and order books) through your public gateway at a low
request rate, and to retain it privately. The purpose is evaluating our own
models: checking how our probabilities compare with where the market was
priced at the same moment, and measuring whether our numbers move toward or
away from the market over time.

What I would not do, and would commit to in writing:

- No Polymarket price, order book or derived market data would ever appear
  on our website, in our app, in anything we publish, or anywhere public.
- No redistribution, resale or sublicensing to anyone.
- No use to build or support a competing venue or data product.
- Nothing beyond internal model evaluation and research.

To be clear about the commercial side, since your terms turn on it: the
models this would help evaluate are ones whose outputs we sell. Your data
itself would never be shown or passed to anyone, but I don't want to
describe this as purely personal use when a business sits behind it.

Separately, I am considering trading on Polymarket US through your official
API. I understand that quotes and fills my own account receives in the
course of trading are a different matter, and I'd welcome confirmation of
how you treat retention of that account-level data.

If this sits better under a commercial data agreement, I'm open to that and
would like to understand pricing. If a written permission for low-rate
internal use is simpler, that would work too.

Thanks,
Doyle Dettro
Predictium
```

## Draft 2 — Polymarket offshore (Adventure One QSS Inc.): §4.2 written consent

```text
Subject: Request for written consent under your Terms of Use for internal research

Hello,

I'd like to ask for the written consent your Terms of Use contemplate, and
I want to describe the use accurately.

Who I am: I run Predictium, a US-based sports analytics business. We build
probabilistic models for sports and sell access to their outputs by
subscription. I understand US persons cannot trade on polymarket.com, and I
am not asking to.

What I'm asking for: consent to read public market data from your gamma and
CLOB endpoints at a low request rate and retain it privately, to evaluate
our own models against the market, comparing our probabilities with where
Polymarket was priced at the same moment.

Your terms bar data-gathering tools without written consent (section 4.2)
and restrict access to the Data by a "Capital Market Client" unless agreed
in writing. I'm raising the second point directly rather than leaving you to
infer it: because we run models commercially, you may regard us as a
financial technology company or similar under that definition. I would
rather you decide that knowing the facts than grant something on an
incomplete picture.

What I would not do, and would commit to in writing:

- No Polymarket price, order book or derived market data would ever appear
  on our website, in our app, in anything we publish, or anywhere public.
- No redistribution, resale or sublicensing to anyone, including to any
  capital markets client or market data distributor.
- No use to build or support a competing venue or data product.
- Nothing beyond internal model evaluation and research.

If a written data agreement is the appropriate route, I'm open to it and
would like to understand the terms. If the answer is no, that's fine too;
I'd rather know than assume.

Thanks,
Doyle Dettro
Predictium
```

---

## Notes for Doyle before sending

1. **Send them from the address you want the relationship on.** A licence
   granted to "Doyle Dettro" and one granted to "Predictium" are different
   things. I've signed both as you on behalf of Predictium, which matches
   who would actually use the data. Change it if that's wrong.
2. **Expect the offshore one to be harder.** A US entity asking an offshore
   venue that bars US trading for data consent is an unusual ask, and they
   may simply decline. It is still worth sending: a no settles the question,
   and the current state is already "not cleared".
3. **Polymarket US is the one that matters, but not because it is deep.**
   It is the venue you could actually trade. Be aware that the ruling's
   premise — "the 0.75-cent NFL touch" — is a number I **corrected** on NFL
   Sunday, 2026-09-20, and failed to commit (see the evaluation doc, Q4).
   Properly paginated, live mid-slate, NFL pre-match on Polymarket US was a
   **7.95-cent touch, 4.51 cents of slippage at Probe, 13.33 at Core, 18.19
   at Scale**. Kalshi on the same slate did **0.77 / 1.74 / 2.90 cents** at
   Probe / Core / Scale on moneylines. Polymarket US lost at every tier. A
   licence is still worth having for evaluation, but it is not a reason to
   prioritise trading there.
4. **Nothing changes while you wait.** Under the ruling, no collector runs on
   either venue regardless of when or whether they reply.
