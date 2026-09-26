# Novig data access — licence findings and draft permission request

_2026-09-20, Books & Odds session. For Portfolio/Risk to relay to Doyle._

**Nothing was sent. No contact has been made with Novig.** This is research
and a draft for Doyle to decide on. He is the customer, not us.

---

## 0. My error, since it is the origin of this

On 2026-08-13 I probed Novig's endpoints and wrote, verbatim: *"ToS posture:
gray, same class as Bovada/FanDuel. Keyless and unauthenticated, but an
undocumented internal API rather than a published data product."*

I asserted a terms posture without reading Novig's terms, and without
checking whether anyone else had. The tennis session had already published
`tennis_prediction_model_2026/docs/novig_endpoint_probe.md` on **2026-07-14**,
a month earlier, which read the actual Terms of Use and reached the opposite
conclusion. My plan then recommended building an adapter on that basis.

Two process lessons, both mine:
1. "Keyless and technically reachable" is a statement about engineering, not
   about permission. I wrote the first and implied the second.
2. Search the org's own committed research before probing. Tennis had done
   the work and I duplicated the probe while missing the conclusion.

The new standing precondition — terms-cleared in writing before scheduled
capture, owned by Books & Odds — is the right fix and should have existed
before I wrote that plan.

---

## 1. What Novig actually offers: a full documented API exists

This is the constructive finding, and it is better than expected.

Novig publishes developer documentation at **docs.novig.com** for the **NBX
API**. It is not a scraping workaround; it is an official product.

What it covers, per the published docs index (retrieved 2026-09-20):

- **Market data**: get open markets, get market, get order book, get markets
  by event, get markets by events (batch, up to 50), get ticks, get lock
  status, get leagues, get event types, get events.
- **Order management**: place, place batch (up to 1024), cancel, cancel
  batch, cancel all, get order, kill switch.
- **Account**: wallet balance, open costs, all my orders, all my fills, all
  my positions, user transactions, analytics dashboard URL.
- **WebSocket streams**: order book channel, market lifecycle channel, with
  JWT auth.
- **RFQ / market-maker pathway**: register your pricer webhook (self-serve),
  maker WebSocket, `/quote` `/confirm` `/ping` webhook endpoints.
- **Maker Credit Program**: cash credits when a make order supplies
  liquidity, in-game on straight contracts and on every fill in NFL and
  NCAAF futures.

**Authentication**: OAuth 2.0 client credentials with JWT, tokens expiring
in 30 minutes. Base URL `https://api.novig.com/nbx/v2`, with a QA
environment at `https://api-qa.novig.us/nbx/v2`. Both in AWS us-east-1; no
PrivateLink offered.

**Access is granted, not self-serve.** The authentication page says, in
full: *"Request your client ID and secret from Novig."* That single sentence
is the entire ask. There is no signup flow, no published price, and no
public tier list.

**Rate limits are generous well beyond our needs** (per route, per method):

event screener (`emm/events`, `emm/events/{id}`, `emm/events/getMarketsByEvent/{id}`) / 1024 per second
all other EMM reads / 256 per second
order placement single / 256 per second
order placement batch / 64 per second
order cancellation / 512 per second
user history (`fills`, `orders`, `transactions`) / 32 burst per second, 512 sustained per 60s, max 256 items per request
kill switch / 1 per 30 seconds

For context on scale: our entire per-sport capture need is on the order of
a few requests per minute. The event screener alone allows 1024 per second.
**Rate limiting is not the constraint; permission is.**

Worth noting for the ask: `emm/events/getMarketsByEvent/{eventId}` "returns
markets with their bid/ask data in a single response, eliminating the need
to fetch orders separately," and the batch variant covers 50 events per
call. Our entire daily tape for a sport is a handful of calls.

## 2. On price: Doyle has a prior answer, and it may be the wrong question

Golf reports that Doyle has already considered a Novig paid API at roughly
**$400–800/month** and deferred it until revenue is up. **I could not
verify that figure**: no price appears anywhere in Novig's public developer
documentation, on their site, or in any primary source I could reach. I am
carrying it as golf's report, not as established fact, exactly as it was
given to me.

If it is right, then the draft below is **not a fresh ask — it is a request
to revisit a decision Doyle already made on cost grounds.** It should be
framed to him that way, and the honest framing is this:

**A written-permission request costs nothing but a letter.** That option was
not on the table when he said no to a subscription. The two are different
asks with different price tags, and conflating them would misrepresent the
decision. Specifically:

- A commercial data licence at $400–800/month buys support, an SLA, and
  contractual certainty. At a $20,000 bankroll that is 24–48% of bankroll
  per year, which is why deferring it was reasonable.
- Written permission for low-rate internal collection buys none of those
  things and costs nothing. It converts an accepted-risk posture into a
  documented one.

The second is what the draft asks for, with the first named as the fallback
we would consider if they prefer to sell rather than permit.

## 3. The account-holder data question, which is load-bearing

Portfolio/Risk asked me to confirm, against the actual terms text, whether
Novig's terms distinguish our own account activity from Service-derived
content — because soccer's `novig_api_orders` view and our whole fill ledger
rest on that reading.

**I could not confirm it, and I am not going to assert it.**

What I tried: `novig.com/legal/terms-and-conditions` and
`www.novig.us/legal/terms-and-conditions` are JavaScript-rendered shells —
80KB of HTML yielding 107 bytes of text — and the `.us` path 301-redirects
to `novig.com/landing`. Headless rendering failed on proxy CA trust in this
environment. `novig.com/terms` returns HTTP 405 to a fetch. Searches
restricted to Novig's own domains returned the Sweepstakes Rules and Help
Centre, not the Terms of Use body.

So the verbatim clause text is **unread by me**. Tennis read it on
2026-07-14 and recorded the substance (prohibits scraping, database
building, permanent copies of Service-derived content); Portfolio/Risk says
they read it too. Neither summary addresses the account-holder-data
distinction, which is the specific question.

**What can be said without the text**, and it is worth saying because it
cuts the right way: the NBX API documents
`GET emm/fills/all`, `emm/orders/all`, `emm/transactions`, and wallet
balance as first-class authenticated endpoints scoped to "the trader
associated with the API key." Novig therefore *offers* programmatic access
to exactly this data, to the account holder, as a product feature. It is
hard to read a general anti-scraping clause as prohibiting a customer from
retaining data Novig builds an endpoint to hand them.

That is an argument, not a clearance. **Recommendation: put the question in
the letter.** One sentence asking them to confirm that our own orders,
fills, positions and transactions are ours to retain costs nothing to ask
and converts a load-bearing assumption into a written answer. It is item 3
in the draft.

Until then, our fill ledger continues on an **assumed** reading. That is
probably fine and probably correct, but it should be logged as assumed
rather than cleared, and it is the single cheapest thing on this page to
fix.

---

## 4. DRAFT — written permission request

For Doyle to send, edit or discard. Scoped narrowly and honestly: every
claim in it is true of us today.

```text
Subject: Request for written permission — internal market data access

Hello,

I'm a funded, active Novig customer and I'd like to get something in
writing rather than assume it.

I run Predictium, a sports analytics business: we build probabilistic
models for sports and sell access to their outputs by subscription. I also
trade on Novig with my own capital. I use Novig prices for two internal
purposes: calibrating our models, and measuring my own execution quality
against the market at the time I traded. I'd like your
written permission to collect and retain Novig market data for those
purposes, at low request rates.

What I'm asking to do:

1. Read public market data (events, markets, order books) for the sports I
   model, at a low polling rate — on the order of a few requests per minute
   per sport, far below the published NBX rate limits.
2. Retain those observations in a private internal database, so that a
   price I saw at the time I traded can be reconstructed later for
   calibration and post-trade analysis.
3. Confirm that my own account activity — my orders, fills, positions and
   transaction history, which the NBX API exposes to me as the account
   holder — is mine to retain and analyse without restriction.

What I will not do:

- I will not redistribute Novig data to anyone.
- I will not display Novig prices publicly, on a website, in an app, or in
  any published material. Nothing I publish will carry a Novig-attributed
  price.
- I will not build or operate a product that competes with Novig, and I
  will not resell or license this data onward.
- I will not use it for anything other than internal modelling,
  calibration and my own post-trade analysis.

To be straightforward about the commercial side: the models this would help
calibrate are ones whose outputs we sell. Novig's data itself would never be
shown or passed to anyone, but I don't want to describe this as purely
private when a business sits behind it.

I'm also open to the NBX API under whatever commercial terms you offer, if
you would rather this sit under a data agreement than a permission letter.
I'd want to understand pricing before committing, and if there is a tier
that covers retaining order book snapshots for internal use, I'd like to
hear about it.

If a permission letter is simpler for you, that works for me too. Either
way I'd rather operate with your explicit agreement than an assumption.

Happy to sign an NDA or a data agreement if that's the cleaner route, and
happy to describe the technical setup in as much detail as useful.

Thanks,
Doyle Dettro
[account identifier]
```

**Corrected 2026-09-26.** The first version of this draft described us as
"a private sports modelling operation" and never said the model outputs are
sold. That omission would have made any permission we got unreliable: a yes
granted on an incomplete description is void the day the counterparty learns
the rest. The draft above now says it plainly. Same fix applied to the
Polymarket drafts, where the terms turn explicitly on "non-commercial" use and
on "financial technology companies".

## 5. What I recommend Doyle do

1. **Send something.** The cost is a letter and the downside is a no, which
   leaves us exactly where we are now — with Novig capture on hold. There is
   no scenario where asking makes our position worse.
2. **Lead with permission, not the subscription.** He already declined the
   subscription on cost; the letter above asks for the free thing first and
   names the paid route as a fallback, which is the honest ordering and the
   one most likely to get a yes.
3. **Get the account-holder-data answer in writing regardless.** Even a
   "no" on collection leaves our fill ledger resting on an unread clause.
   Item 3 of the letter is the cheapest risk reduction available to us.
4. **Keep capture on hold until an answer arrives.** Not because the risk is
   acute — no Predictium code touches a Novig domain today, so exposure was
   prospective only — but because the new precondition is the right standard
   and we should not breach it in its first week.

## 6. Precedent, presented as precedent and not as permission

Golf's licensing inventory records that Doyle has knowingly accepted this
class of risk before, in writing, on named venues:

- **FanDuel**: INTERNAL_ONLY, "ratified accepted-org-risk book logger",
  with the strict reading retained that FanDuel's terms "prohibit
  robots/scrapers/automated access without written permission."
- **Bovada**: same posture, strict reading retained that §§8.1 and 9.9
  "restrict content to private personal use and prohibit commercial use
  without a license."

So there is an established pattern: accept the risk explicitly, contain the
data to internal use, never publish raw or book-attributed prices, and keep
the strict reading visible on the record rather than arguing it away.

**This is context for his call, not an argument for either side of it.**
Novig differs from those two in one way that matters and cuts toward asking
rather than accepting: we are a funded, paying, identified customer with an
account they can see. An anonymous scraper has nothing to lose from a
terms breach. We have a real-money account, open positions, and a
relationship — which is both why a breach would cost more, and why a
permission request is a realistic ask rather than a long shot.
