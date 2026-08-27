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
.venv/Scripts/python -m pytest -q                         # 194 passed
.venv/Scripts/python -m reclaim.cli serve                 # dashboard on :8000
```

Every command takes `--json`.

**The batch is seeded.** `seed 42` produces the same 120 records, the same
`₹6,92,056`, and the same timestamps on every machine — `cli verify` compares a
digest of every field, not just the total, because amounts that reproduce while
timestamps drift is how compliance counts quietly move between runs. Numbers below
are meant to be reproduced, not trusted.

---

## The run

```
BATCH RESULTS  (n = 120 at-risk records)

  Money at risk                    ₹6,92,056
  Money recovered                    ₹80,183   (11.6% by value, 31.7% by record)
  Still open                       ₹4,77,600
  Written off / unrecoverable      ₹1,34,273   (never-retry causes, escalated not chased)

  Recovery rate by root cause
    BANK_DOWNTIME           100%    22/22       ₹40,629     <- cohort signal, 0 contacts
    CART_ABANDONMENT         39%     7/18       ₹15,268
    AUTH_DROPOFF             28%     5/18        ₹3,623
    EXPIRED_INSTRUMENT       24%     4/17       ₹20,663
    UNKNOWN                   0%     0/38            ₹0     <- layer 2 offline, sent to a human
    POLICY_BLOCK              0%     0/4             ₹0     <- correctly escalated, never chased
    MANDATE_REVOKED           0%     0/3             ₹0

  Guardrails: 229 refusals across 88 records
  Human escalations               47
  Interventions executed          99   (75 contacts, 24 silent)
  Contacts per recovery         1.97
  Outcomes attributed             99   via verified webhooks
```

`recovered + open + written off == at risk`, asserted by `cli verify` and by
`tests/test_scoreboard.py`. A scoreboard that does not add up is one where a rupee
got counted twice, and nothing crashes when that happens.

---

## The baseline — including where we lose

PROJECT.md §9 calls this mandatory, and it is the one section where it would be
easy to quietly not publish the result.

| | Naive | ReclaimAI |
|---|---:|---:|
| Recovered | ₹2,30,905 | ₹80,183 |
| Recovery rate (records) | 41.7% | 31.7% |
| Customer contacts | 272 | **75** |
| Contacts per recovery | 5.44 | **1.97** |
| Contacts to opted-out customers | 20 | **0** |
| Contacts to customers on DND | 62 | **0** |
| Contacts inside quiet hours | 124 | **0** |
| Retries against never-retry causes | 30 | **0** |
| **Contacts our guardrails refuse** | **386** | **0** |

**The naive strategy recovers more money.** Both runs draw their coin flips from
the same seeded stream keyed on `(record, attempt)`, so record `REC_5041`'s second
attempt succeeds or fails identically under both — nothing separates them except
what each chose to do, when, and to whom.

So `cli baseline` itemises every rupee of the gap:

```
₹1,50,722 the naive run collects and we do not:
    8    ₹63,287   customer opted out or on DND - contact refused
    1    ₹75,587   above the value ceiling - routed to a human
    4    ₹10,541   diagnosed UNKNOWN - layer 2 unavailable
   12    ₹19,594   still in flight - deferred, not abandoned

₹1,38,357 of that is money the agent was told not to take.
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
PASS  13 guardrails implemented
PASS  webhook signature verifies raw bytes
PASS  webhook handles the five outcome events
PASS  API exposes every documented route
PASS  no action executed twice
PASS  scoreboard balances
PASS  every recovered rupee traces to an intervention
PASS  baseline gap is fully accounted for
PASS  dashboard is built and servable
TODO  deterministic map matches harvested codes  (needs `cli harvest`)
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
one from a fixed list, which the policy table and the thirteen guardrails below it
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

Dashboard: **https://reclaimai-eight.vercel.app** — live. It reports "no API
connected" until the Render half is deployed, which is the honest state rather
than an error: the front end has nothing to read from yet.

```
   Vercel  ──────────►  Render  ◄──────────  Razorpay
  dashboard   /api/*      API        webhook   test mode
```

**The point of deploying is the webhook, not the hosting.** Everything else here
has been exercised; a public URL is the only thing standing between this project
and a genuine Razorpay delivery. Render's free instances cold-start in 30-50
seconds, so drive the demo locally with `reclaim serve` and let the deployment
receive.

`SEED_ON_BOOT` generates one batch if the database is empty at startup, guarded on
the table being empty rather than the flag alone, so a restart cannot wipe a batch
somebody is presently demonstrating. `CORS_ORIGINS` must name the Vercel URL
exactly — the dashboard and the API are different origins, and the browser blocks
every call otherwise.

## Honesty note

Real Razorpay APIs, real error-code shapes, real payment links, real HMAC
verification, real attribution. **Customer response is modelled** by
[synthetic/outcomes.py](reclaim/synthetic/outcomes.py) — per-cause success
probabilities, stated openly rather than presented as live conversion data.

Three things are built and tested but have not been exercised against the live
world, and are listed here rather than left for a judge to find:

1. **Layer 2 has never made a real API call** (no `ANTHROPIC_API_KEY`).
2. **No webhook has arrived from Razorpay** (no tunnel) — the receiver is driven
   by locally signed payloads through the same endpoint.
3. **`DETERMINISTIC_MAP` still keys off Razorpay-shaped guesses** — four payment
   links are minted and unpaid on the test account; `cli harvest --collect`
   replaces the guesses with harvested codes, and `cli verify` reports that check
   as `TODO` until it does.
