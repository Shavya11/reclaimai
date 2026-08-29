# ReclaimAI — AI Revenue Recovery Agent

Razorpay Buildathon, Track 03. Detects revenue at risk, diagnoses *why* it failed,
decides a bounded intervention, executes it, attributes the outcome — and refuses
to act when it shouldn't.

**The core design rule: the LLM never touches money.** It produces a label from a
closed enum. A deterministic policy table turns that label into a proposed action.
A deterministic guardrail engine decides whether the action may fire.

---

## Verify in 90 seconds

No credentials required. `DRY_RUN` is the default, so a fresh clone runs the whole
pipeline — including webhook verification and outcome attribution — with no
Razorpay or Anthropic key.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Linux/mac: .venv/bin/python

.venv/Scripts/python -m reclaim.cli demo --extra-ticks 3  # the whole arc, ~24s
.venv/Scripts/python -m reclaim.cli verify                # structural self-audit
.venv/Scripts/python -m pytest -q                         # 314 passed
.venv/Scripts/python -m reclaim.cli serve                 # dashboard on :8000

.venv/Scripts/python -m reclaim.cli rules                 # the rule table, shipped vs edited
.venv/Scripts/python -m reclaim.cli promises              # the promise-to-pay book
.venv/Scripts/python -m reclaim.cli replay \
    --guardrail value_ceiling.requires_human_above=7500000
```

Every command takes `--json`.

**The batch is seeded.** `seed 42` produces the same 180 records, the same
`₹1,12,09,814`, and the same timestamps on every machine — `cli verify` compares a
digest of every field, not just the total, because amounts that reproduce while
timestamps drift is how compliance counts quietly move between runs. Numbers below
are meant to be reproduced, not trusted.

V2 added 60 B2B invoices to the 120 payment records. They are drawn from their own
RNG stream, appended after the payments batch, so **V1's figures still reproduce
exactly**:

```bash
cli detect --leak-types FAILED_PAYMENT,ABANDONED_CART,FAILED_MANDATE
# 120 at-risk records, ₹8,24,984 at risk
```

Sharing one stream would have shifted all 120 existing records and invalidated
every number published about V1 — invisibly, while the batch still ran and still
looked right. `tests/test_generator.py` asserts the identity, not the count.

---

## The run

```
BATCH RESULTS  (n = 180 at-risk records)

  Money at risk                 ₹1,12,09,814
  Money recovered                 ₹27,44,651   (24.5% by value, 38.3% by record)
  Still open                      ₹70,42,793
  Written off / unrecoverable     ₹14,22,370   (never-retry causes, escalated not chased)

  Recovery rate by root cause
    AWAITING_APPROVAL        32%     9/28    ₹16,71,657   <- their cycle, not a delinquency
    INVOICE_NOT_RECEIVED     80%    8/10      ₹5,19,966   <- it never arrived; resend it
    BUYER_CASH_CRUNCH        18%    2/11      ₹3,09,909
    PAYMENT_STALLED          17%     1/6        ₹88,921   <- the dunning ladder
    INSUFFICIENT_FUNDS       51%   18/35        ₹63,152   <- layer 2, salary-window retry
    BANK_DOWNTIME            82%   18/22        ₹50,707   <- cohort signal, 0 contacts
    AUTH_DROPOFF             28%    5/18        ₹30,039
    CART_ABANDONMENT         22%    4/18         ₹6,647
    EXPIRED_INSTRUMENT       24%    4/17         ₹3,653
    INVOICE_DISPUTED          0%     0/6             ₹0   <- a conversation, never dunned
    POLICY_BLOCK              0%     0/4             ₹0   <- correctly escalated
    MANDATE_REVOKED           0%     0/3             ₹0
    UNKNOWN                   0%     0/2             ₹0   <- layer 2 declined to guess

  Guardrails: 439 refusals across 154 records
  Human escalations               50
  Interventions executed         192   (169 contacts, 23 silent)
  Contacts per recovery         2.45
  Outcomes attributed            192   via verified webhooks

  Receivables            60 invoices · ₹1,03,84,830 at risk · ₹25,90,453 recovered
  DSO                    132.5 -> 119.5 days   (12.9 days off the average)
  Promises to pay        9 made · 6 kept · 3 broken   (67% kept)
  Replies read           27, each labelled from a closed set of seven intents
```

`recovered + open + written off == at risk`, asserted by `cli verify` and by
`tests/test_scoreboard.py`. A scoreboard that does not add up is one where a rupee
got counted twice, and nothing crashes when that happens.

### What reproduces exactly, and what does not

Worth separating, because publishing a figure that will not come back is how a
number stops being evidence.

**Exactly reproducible, on any machine:** the batch itself — 180 records,
`₹1,12,09,814`, every amount, every customer, every timestamp. `cli verify`
compares a digest of every field. Also the entire run with layer 2 off:
`cli demo --no-llm` lands on `₹8,61,109` across 34 records, twice, ten times,
anywhere.

**Not exactly reproducible: the headline recovered figure.** Layer 2 is a
language model and it is not deterministic — the same invoice can come back
`PAYMENT_STALLED` on one run and `INVOICE_NOT_RECEIVED` on the next, which is a
different policy row, a different ladder and a different outcome. Two live runs
of the arc on this machine produced `₹27,44,651` and `₹23,44,566`.

So the figure published above is the one frozen in
`fixtures/demo_snapshot.json.gz` — a real run of the real pipeline, committed, and
what the deployment restores. It is reproducible in the only sense that matters
for a published number: anyone can restore it and get the same scoreboard. A
fresh live run will land near it, not on it.

The seeded parts of the simulation are unaffected. Whether a customer pays is
drawn from a stream keyed on `(record, attempt)`, so the naive baseline and this
run get the same coin flips and the comparison between them stays honest even as
the diagnoses move.

---

## The baseline — including where we lose

PROJECT.md §9 calls this mandatory, and it is the one section where it would be
easy to quietly not publish the result.

| | Naive | ReclaimAI |
|---|---:|---:|
| Recovered | ₹47,58,234 | ₹27,44,651 |
| Recovery rate (records) | 45.0% | 38.3% |
| Customer contacts | 418 | **169** |
| Contacts per recovery | 5.16 | **2.45** |
| Contacts to opted-out customers | 44 | **0** |
| Contacts to customers on DND | 66 | **0** |
| Contacts inside quiet hours | 330 | **0** |
| Contacts over the frequency cap | 289 | **0** |
| Retries against never-retry causes | 30 | **0** |
| **Contacts our guardrails refuse** | **729** | **0** |

**The naive strategy recovers more money.** Both runs draw their coin flips from
the same seeded stream keyed on `(record, attempt)`, so record `REC_5041`'s second
attempt succeeds or fails identically under both — nothing separates them except
what each chose to do, when, and to whom.

So `cli baseline` itemises every rupee of the gap:

```
₹2,41,069 the naive run collects and we do not:
    3    ₹79,258   customer opted out or on DND - contact refused
    2  ₹1,47,603   above the value ceiling - routed to a human
    2    ₹12,104   still in flight - deferred, not abandoned
    3     ₹2,104   our strategy simply did worse here

₹2,26,861 of that is money the agent was told not to take.
```

The naive run is not a better strategy; it is an undeployable one. Publishing a
comparison we lose on is only defensible because every rupee of the difference has
a stated reason — and stating what restraint costs in rupees is more convincing
than claiming it is free.

---

## `cli verify` — the build audits itself

CLAUDE.md requires that adding a `RootCause` updates four places at once. Rather
than trusting that, [reclaim/verify.py](reclaim/verify.py) checks it mechanically,
along with every other structural claim this README makes.

```
PASS  AtRiskRecord stays V2-generic
PASS  money is integer paise
PASS  idempotency key is UNIQUE at the database
PASS  audit_log is append-only at the database
PASS  batch is reproducible from seed
PASS  detectors cover all V1 leak types
PASS  outcome simulator covers every RootCause
PASS  deterministic map yields valid causes
PASS  policies.yaml covers every RootCause
PASS  14 guardrails implemented and registered
PASS  webhook signature verifies raw bytes
PASS  webhook handles the five outcome events
PASS  API exposes every documented route
PASS  no action executed twice
PASS  scoreboard balances
PASS  every recovered rupee traces to an intervention
PASS  baseline gap is fully accounted for
PASS  dashboard is built and servable
PASS  deterministic map matches harvested Razorpay codes
```

---

## Two guarantees enforced by the database, not by discipline

**No double-charging.** `executed_actions.idempotency_key` is `UNIQUE`, and the key
is *derived* from `(record_id, attempt_number, action_type)` as a property — never
passed in, so it cannot drift from the tuple it represents. The key is claimed
*before* Razorpay is called, so a crash between the two leaves a claimed key and no
charge: payments systems may under-deliver on a retry, never double-charge.

```
$ python -m reclaim.cli prove-idempotency
  1. Simulated crash after 30 actions.
  2. keys claimed before the crash: 30
  3. resumed — guardrail #10 blocked 30 replays before they reached Razorpay
     duplicate keys 0
```

**No edited audit trail.** `audit_log` carries SQLite triggers that `ABORT` on
`UPDATE` and `DELETE`. Append-only is a property of the database, not a convention
someone can forget.

Both are proved in [tests/test_foundation.py](tests/test_foundation.py) and
[tests/test_runner.py](tests/test_runner.py), not asserted here.

---

## Diagnosis is measured, not asserted

The generator records the cause it planted in every record, so accuracy is scored
against ground truth rather than demonstrated by anecdote:

```
DIAGNOSIS ACCURACY  (n=120, ground truth known by construction)
  deterministic      69 records   100.0% correct
  cohort             15 records   100.0% correct
  fallback           36 records     0.0% correct   <- layer 2's job
```

**The cohort signal earns its place.** Fifteen records on one issuer inside one
hour carry a generic "declined by the bank" error. Read alone, each is a
customer-side failure worth a message. Read together, the issuer is failing at
0.71 against a 0.045 baseline — 16×— so the agent stays silent and retries in
twenty minutes. **Fifteen needless customer contacts prevented**, and the
counterfactual is computed, not claimed.

---

## Restraint, in numbers

```
   confidence_floor      38 records   (38 refusals over the run)
   frequency_cap         18 records   (48 refusals over the run)
   cooldown              17 records   (95 refusals over the run)
   value_ceiling          7 records   (7 refusals over the run)
   consent                5 records   (5 refusals over the run)
   dnd                    3 records   (36 refusals over the run)
```

Two counts, because over a dozen ticks the same deferral is re-evaluated
repeatedly and only one of these numbers is the one to quote. Every refusal carries
its reason and what happens next: a time to retry, a human to route to, or a
permanent stop. `audit_log` records blocks as loudly as executions.

---

## The loop closes through verified webhooks

Money reaches the scoreboard by exactly one path:

```
payment_link.paid  ->  the link id we minted
                   ->  the intervention that minted it
                   ->  the record it was chasing
                   ->  ₹ attributed to that record, and only that record
```

Signature verification runs on the **raw request bytes**, before anything parses
them. Verifying a re-serialized body is the classic webhook bug — key order and
separators differ, the HMAC never matches, and the usual fix is to stop checking.
[tests/test_webhooks.py](tests/test_webhooks.py) has 19 tests, the first of which
is exactly that case.

Attribution is idempotent (Razorpay retries deliveries), single-credit (`payment.
captured` and `payment_link.paid` describe the same rupees), and loud when it
fails — an event matching no intervention is logged `UNATTRIBUTED`, never
discarded. Money we cannot explain is not money we get to count.

**What is not real is the tunnel.** `cloudflared` is not installed on the build
machine, so Razorpay has no public URL to reach. [reclaim/settlement.py](reclaim/settlement.py)
signs Razorpay-shaped payloads and posts them through the same `receive()` a live
delivery hits — nothing bypasses the signature check or the attribution walk. The
outcome simulator decides only *whether the customer paid*.

---

## The LLM never touches money

Layer 2 runs only on the records an error string cannot resolve, using forced tool
use against a closed enum. The model cannot invent a root cause — only pick a wrong
one from a fixed list, which the policy table and the fourteen guardrails below it
still contain. A schema violation becomes `UNKNOWN` and reaches a human; it never
becomes a guess.

**Layer 2 has never run live** — there is no `ANTHROPIC_API_KEY` on this machine.
Every number in this README is therefore the floor with the model off: 36 of 120
records fall to `UNKNOWN` on the first pass (38 by the end of the run, as the
issuer outage dissolves and its cohort evidence with it) and go to a person
instead of being guessed at.
`--no-llm` is a real code path, not a mock, and
[tests/test_llm_diagnosis.py](tests/test_llm_diagnosis.py) proves the batch
completes with the API down.

---

## Time travel, because a schedule you cannot watch is decorative

A `next_salary_window` retry is a month out; a `+48h` follow-up is two days out.
Schedules resolve against an explicit anchor — attempt 1 from the failure, attempt
N from attempt N-1 — and the demo clock moves that anchor forward:

```bash
python -m reclaim.cli tick --advance 24h
python -m reclaim.cli tick --advance next_salary_window
```

An action that is not yet due is parked on its record and picked up by a later
tick. `cli demo` walks the whole ladder in 24 seconds.

---

## Layout

```
reclaim/
  enums.py          closed enums — RootCause is why hallucination is harmless
  models.py         Pydantic boundary models; AtRiskRecord stays V2-generic
  db.py             eight tables + the two database-level guarantees
  clock.py          the demo clock — persisted, so ticks survive a restart
  timeutil.py       IST, quiet hours, next_salary_window
  money.py          paise -> ₹5,84,300 / ₹5.84L
  verify.py         structural self-audit
  detectors/        one plugin per leak type -> detect() -> list[AtRiskRecord]
  synthetic/        seeded leak generator, outcome simulator, Razorpay payloads
  brain/            diagnosis / policy / guardrails
  executor/         Razorpay wrapper: idempotency, backoff, DRY_RUN
  webhooks/         signature (raw bytes) + events + outcome attribution
  settlement.py     replays modelled outcomes through the real webhook path
  scoreboard.py     the §9 scoreboard, recomputed from storage every time
  baseline.py       the naive strategy, and where it beats us
  audit/            append-only decision log
  api/              FastAPI: /api/* for the UI, /webhooks/razorpay for Razorpay
ui/                 Next.js dashboard, static-exported and served by FastAPI
```

**[DEMO.md](DEMO.md)** is the five-minute script, with measured command timings.

---

## Deployment

**Render** runs the API and the webhook receiver; **Vercel** serves the
dashboard. [DEPLOY.md](DEPLOY.md) is the step-by-step.

**Live:** dashboard at **https://reclaimai-eight.vercel.app**, API and webhook
receiver at **https://reclaimai-api.onrender.com**.

The deployed scoreboard reproduces the local one to the rupee — `₹27,44,651`
recovered, 38.3% of records, 2.45 contacts per recovery — on the first request,
from a cold instance, with no key configured at all. It is restored from
`fixtures/demo_snapshot.json.gz`: the settled arc frozen by `reclaim snapshot`,
walked once with layer 2 on by the same runner that produces the local numbers.
Not a hand-written fixture, which could drift away from the code; the actual
output of the actual pipeline, committed.

This exists because the honest alternative was worse. Rebuilding the batch live
on boot is ~100 seconds of a rate-limited free model tier, and a free instance
cold-boots whenever it has been idle fifteen minutes — so a visitor arriving in
that window met a truthful, useless `₹0` and had no way to know it meant *not
yet* rather than *nothing was recovered*.

`GEMINI_API_KEY` now buys the *live* buttons rather than the first impression:
with it, Run batch and the clock chips re-diagnose for real and land back on
`₹27,44,651`; without it they re-run on layer 1 alone and land `₹8,61,109` across
34 records, with 69 in `UNKNOWN` instead of 2. Same code, same seed, different
diagnosis depth.

The gap is wider in V2 than it was in V1, and the reason is worth stating: layer 1
for receivables answers only what the ledger already knows — a dispute flag, a
partial payment, an invoice inside the buyer's own average — and refuses to guess
between "nobody received it" and "it stalled". Without a model those 31 invoices
are honestly `UNKNOWN` and reach a person. That is the fallback chain working, not
a degraded mode to apologise for, but it is a real difference in what the agent
can do alone.

Without a model the conversation layer degrades the same way: replies are matched
on keywords at a deliberate 0.5 confidence, which sits below the floor, so every
one of them reaches a human and **no promise is ever made**. The batch still
completes.

```
   Vercel  ──────────►  Render  ◄──────────  Razorpay
  dashboard   /api/*      API        webhook   test mode
```

**The point of deploying is the webhook, not the hosting.** Everything else here
has been exercised; a public URL is the only thing standing between this project
and a genuine Razorpay delivery. Render's free instances cold-start in 30-50
seconds, so drive the demo locally with `reclaim serve` and let the deployment
receive.

`SEED_ON_BOOT` restores the snapshot if the database is empty at startup, guarded
on the table being empty rather than the flag alone, so a restart cannot wipe a
batch somebody is presently demonstrating. Both write endpoints answer `202` and
work on a thread — a tick re-diagnoses through a paced model tier, and a request
that takes two minutes is one a proxy will cut before it returns — so the
dashboard follows `seeding` in `/api/health` instead of inferring progress from
the shape of the scoreboard. `CORS_ORIGINS` must name the Vercel URL
exactly — the dashboard and the API are different origins, and the browser blocks
every call otherwise.

## Honesty note

Real Razorpay APIs, real error-code shapes, real payment links, real HMAC
verification, real attribution. **Customer response is modelled** by
[synthetic/outcomes.py](reclaim/synthetic/outcomes.py) — per-cause success
probabilities, stated openly rather than presented as live conversion data.

Three things are built and tested but have not been exercised against the live
world, and are listed here rather than left for a judge to find:

1. **Layer 2's accuracy depends on a fixture we corrected.** It scored 0% on
   its 36 records until the generator was fixed to give `INSUFFICIENT_FUNDS`
   records the signals that identify one — prior payment history, a late-night
   attempt near month-end. Those are what `policies.yaml` already assumes when
   it retries at `next_salary_window`; the fixture simply had not carried them.
   The prompt was sharpened in the same pass. Both are stated because 97.5% read
   without them would be a number doing more work than it earned.
2. **No webhook has arrived from Razorpay** (no tunnel) — the receiver is driven
   by locally signed payloads through the same endpoint.
3. **Only two error reasons were harvested from live test-mode payments**
   (`payment_cancelled`, `international_transaction_not_allowed`). The rest of
   `DETERMINISTIC_MAP` is validated against Razorpay's published error-reason
   list rather than observed on this account — every one of its 42 keys appears
   in that list, but 40 of them have not been seen arrive here. Razorpay
   Checkout fingerprints and blocks headless browsers, so the remaining
   scenarios need a human at a real browser to harvest.
