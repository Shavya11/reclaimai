# ReclaimAI — AI Revenue Recovery Agent

Razorpay Buildathon, Track 03. Detects revenue at risk, diagnoses *why* it failed,
decides a bounded intervention, executes it, attributes the outcome — and refuses
to act when it shouldn't.

**The core design rule: the LLM never touches money.** It produces a label from a
closed enum. A deterministic policy table turns that label into a proposed action.
A deterministic guardrail engine decides whether the action may fire.

---

## Every claim, and how to check it

Each row names the code that does the thing, the test that asserts it, and the
number it produced on `seed 42`. Nothing here is a figure typed into a document.

| Claim | Code | Test | Measured |
|---|---|---|---|
| **Razorpay delivered, and we verified it** | [webhooks/attribution.py](reclaim/measure/webhooks/attribution.py) | [tests/test_webhooks.py](tests/test_webhooks.py), evidence in [evidence/webhook.json](evidence/webhook.json) | 5 events, all `simulated: false`, walked to `REC_5085` |
| A customer never gets a third message in 7 days | [rules/frequency_cap.py](reclaim/guardrails/rules/frequency_cap.py) | `test_invariant_no_customer_exceeds_two_contacts_in_seven_days` | 70 refusals |
| No action tuple ever executes twice | [rules/idempotency.py](reclaim/guardrails/rules/idempotency.py) | `test_invariant_no_action_tuple_ever_executes_twice` | 192 executions, 192 distinct keys |
| An opted-out customer is never contacted | [rules/consent.py](reclaim/guardrails/rules/consent.py) | `test_opted_out_customer_never_contacted` | 15 refusals, 0 contacts |
| Silent retries are exempt at 3am; SMS is not | [rules/quiet_hours.py](reclaim/guardrails/rules/quiet_hours.py) | `test_silent_retry_allowed_at_3am_but_sms_is_not` | 23 silent retries |
| A flagged card is never retried | [policies.yaml](reclaim/decide/policies.yaml) | `test_risk_decline_never_produces_an_action` | 0 retries |
| A guardrail never raises, even on junk | [guardrails/base.py](reclaim/guardrails/base.py) | `test_guardrails_never_raise` | 500 malformed inputs, all blocked |
| The batch completes with the model down | [diagnosis/engine.py](reclaim/diagnose/engine.py) | `test_batch_completes_when_the_api_is_down` | 180/180 records |
| A forged webhook is rejected | [webhooks/signature.py](reclaim/measure/webhooks/signature.py) | `test_a_tampered_body_fails_verification` | raw-byte HMAC |
| Every recovered rupee traces to an intervention | [webhooks/attribution.py](reclaim/measure/webhooks/attribution.py) | `test_recovered_money_equals_what_was_attributed` | ₹27,44,651 across 69 records |
| No contact lands inside a promise window | [rules/promise_window.py](reclaim/guardrails/rules/promise_window.py) | `test_invariant_no_contact_lands_inside_a_promise_window` | 2 refusals |
| A settled record leaves the human queue | [human_queue.py](reclaim/decide/human_queue.py) | `test_a_record_escalated_then_paid_leaves_the_queue` | 51 raised, 1 self-resolved |
| The what-if replay writes nothing | [whatif.py](reclaim/measure/whatif.py) | `test_a_replay_changes_nothing_in_the_live_database` | 3,848 rows unchanged |
| Layer 2 earns its calls | [experiments/ablation.py](reclaim/measure/ablation.py) | `tests/test_ablation.py`, evidence in [evidence/ablation.json](evidence/ablation.json) | +₹6,07,926 net, 38 fewer escalations |
| An unprompted payment is never claimed as ours | [webhooks/attribution.py](reclaim/measure/webhooks/attribution.py) | `test_an_unprompted_payment_is_not_credited_to_an_intervention` | logged `ORGANIC`, ₹0 credited |

`cli verify` runs 30 structural checks on every run, and `pytest` runs 446
tests. Both are one command, below.

---

## What is real, and what is modelled

The most common question about a hackathon number, answered before it is asked.

| Real | Modelled |
|---|---|
| Razorpay orders, payment links and error codes — live test-mode API | **Whether a given customer pays**, and **whether they would have paid anyway.** Both in [outcomes.py](reclaim/synthetic/outcomes.py) |
| One genuine Razorpay delivery — five events for `REC_5085`, `simulated: false`, taken on the deployed instance and committed to [evidence/webhook.json](evidence/webhook.json) | Every outcome in the shipped batch — 74 events, all signed locally and stored `simulated: true` |
| Error strings checked against Razorpay's published list — which caught 16 map keys and 3 generator strings Razorpay never emits | Which customer is opted out, on DND, or has a payment history |
| Webhook HMAC-SHA256 over raw bytes, forgery and replay both refused | The timing of an inbound reply |
| The attribution walk from a paid link back to the intervention that minted it | |
| Every model call — layer 2 runs live on `gemini-3.5-flash-lite` | |
| The policy engine, all 14 guardrails, and every refusal | |

Three limitations we would rather state than be asked about:

1. **Customer response is simulated**, and the per-cause priors are *stated
   estimates*, not measured rates. They are published so they can be argued with.
   This also means the recovery-rate-by-cause chart partly reflects numbers we
   chose.
2. **Self-cure is modelled, and its rates are the least defensible numbers
   here.** 34 of 180 customers pay unprompted in this world, drawn per cause
   from `SELF_CURE` in [outcomes.py](reclaim/synthetic/outcomes.py). The
   *ordering* is arguable — an outage clears itself, a dead card does not — but
   the magnitudes are guesses, and the incremental figures move directly with
   them. Nothing measured them.
3. **Layer 2 gets one wrong that matters.** Of three `RISK_DECLINE` records it
   correctly refuses two and reads the third as `INSUFFICIENT_FUNDS` at high
   confidence. It is in the audit trail and it is the honest answer to "what
   does your model get wrong".

---

## Verify in 90 seconds

No credentials required. `DRY_RUN` is the default, so a fresh clone runs the whole
pipeline — including webhook verification and outcome attribution — with no
Razorpay or Anthropic key.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Linux/mac: .venv/bin/python
.venv/Scripts/python -m pip install -e . --no-deps        # optional: makes `reclaim <cmd>` a command

.venv/Scripts/python -m reclaim.cli demo --extra-ticks 3  # the whole arc, ~24s
.venv/Scripts/python -m reclaim.cli verify                # structural self-audit
.venv/Scripts/python -m pytest -q                         # 446 passed
.venv/Scripts/python -m reclaim.cli serve                 # dashboard on :8000

.venv/Scripts/python -m reclaim.cli trace REC_5001        # one record, detection to money, one screen
.venv/Scripts/python -m reclaim.cli rules                 # the rule table, shipped vs edited
.venv/Scripts/python -m reclaim.cli promises              # the promise-to-pay book
.venv/Scripts/python -m reclaim.cli replay \
    --guardrail value_ceiling.requires_human_above=7500000
```

Every command takes `--json`.

**The walkthrough video was recorded at commit `16588a1`.** The repo has moved
since: the suite grew from 342 to 446 tests, `cli verify` from 25 to 30 checks,
and the demo script's record ids were corrected against the batch. Every figure
the video reads off the dashboard — ₹1,12,09,814 at risk, ₹27,44,651 recovered,
38.3% of records, 2.45 contacts per recovery, 439 refusals across 154 records —
is restored from `fixtures/demo_snapshot.json.gz` and reproduces exactly as shown.

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
    confirmed by Razorpay                 ₹0   (0 record(s), webhook not simulated)
    modelled outcome              ₹27,44,651   (simulator decided, real attribution chain)
  Still open                      ₹70,58,295
  Written off / unrecoverable     ₹14,06,868   (never-retry causes, escalated not chased)

  Recovery rate by root cause
    AWAITING_APPROVAL        32%     9/28    ₹16,71,657   <- their cycle, not a delinquency
    INVOICE_NOT_RECEIVED     80%     8/10     ₹5,19,966   <- it never arrived; resend it
    BUYER_CASH_CRUNCH        18%     2/11     ₹3,09,909
    PAYMENT_STALLED          17%     1/6        ₹88,921   <- the dunning ladder
    INSUFFICIENT_FUNDS       53%    18/34       ₹63,152   <- layer 2, salary-window retry
    BANK_DOWNTIME            82%    18/22       ₹50,707   <- cohort signal, 0 contacts
    AUTH_DROPOFF             28%     5/18       ₹30,039
    CART_ABANDONMENT         22%     4/18        ₹6,647
    EXPIRED_INSTRUMENT       24%     4/17        ₹3,653
    INVOICE_DISPUTED          0%     0/6             ₹0   <- a conversation, never dunned
    POLICY_BLOCK              0%     0/4             ₹0   <- correctly escalated
    UNKNOWN                   0%     0/3             ₹0   <- layer 2 declined to guess
    MANDATE_REVOKED           0%     0/3             ₹0

  Guardrails: 439 refusals across 154 records
  Human escalations               51
  Interventions executed         192   (169 contacts, 23 silent)
  Contacts per recovery         2.45
  Outcomes attributed            192   via verified webhooks

  Receivables            60 invoices · ₹1,03,84,830 at risk · ₹25,90,453 recovered
  DSO                    146.5 -> 133.6 days   (12.9 days off the average)
  Promises to pay        9 made · 6 kept · 3 broken   (67% kept)
  Replies read           27, each labelled from a closed set of seven intents
```

`recovered + open + written off == at risk`, asserted by `cli verify` and by
`tests/test_scoreboard.py`. So is `confirmed + modelled == recovered`: the headline
is split by who said the money arrived, read off each webhook row's `simulated`
flag, and the shipped batch is honest about being entirely modelled. The one
real Razorpay delivery this project has received landed on the deployed instance
against a record the snapshot had already settled, so it sits in
[evidence/webhook.json](evidence/webhook.json) rather than in this column. A live
run that Razorpay confirms moves money into it, and nothing else does. A scoreboard that does not add up is one where a rupee
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
| Recovered (headline) | ₹47,58,234 | ₹27,44,651 |
| **Incremental — net of customers who would have paid anyway** | **₹27,50,189** | **₹18,97,274** |
| Self-cures claimed as its own | 21 records | 17 records |
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
₹23,52,777 the naive run collects and we do not:
   11  ₹12,33,478   customer opted out or on DND - contact refused
    7  ₹11,05,485   above the value ceiling - routed to a human
    2     ₹12,104   still in flight - deferred, not abandoned
    3      ₹1,710   our strategy simply did worse here

₹23,38,963 of that is money the agent was told not to take.
```

Of the whole gap, **₹1,710 across 3 records is the only part where our strategy
was simply worse.** Everything else is a rule doing what it was written to do.

### Most of the naive lead is other people's money

34 of the 180 customers would have paid with no prompting at all — a fact the
simulator now models, because a world where nobody ever pays unless chased is a
world that flatters any agent that chases. Both strategies are handed the same
34, drawn from the planted cause on a dedicated stream, so neither is measured
against a world the other did not get.

A strategy that contacts everybody absorbs more of them. The naive run banked
**21** self-cures and counts every one as a recovery it caused; we banked 17. Net
of both, the honest comparison is **₹27,50,189 against ₹18,97,274** — the gap
falls from ₹20.1L to ₹8.5L, and **well over half of the naive strategy's
apparent advantage turns out to be money that was arriving anyway.**

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
PASS  every leak type has a detector
PASS  outcome simulator covers every RootCause
PASS  deterministic map yields valid causes
PASS  deterministic map matches harvested Razorpay codes
PASS  policies.yaml covers every reachable cause
PASS  14 guardrails implemented and registered
PASS  webhook signature verifies raw bytes
PASS  webhook handles the five outcome events
PASS  API exposes every documented route
PASS  no action executed twice
PASS  scoreboard balances
PASS  every recovered rupee traces to an intervention
PASS  baseline gap is fully accounted for
PASS  shipped rules pass the admin validator
PASS  rule_change_log is append-only
PASS  promise states are closed and resolved ones are dated
PASS  no settled record is still queued for a human
PASS  self-cure is the same in both arms
PASS  what-if replay leaves no trace
PASS  visitor records stay out of the published figures
PASS  sandbox preview leaves no trace
PASS  demo script records still match the batch
PASS  README headline matches the scoreboard
PASS  dashboard is built and servable
```

---

## Two guarantees enforced by the database, not by discipline

**No double-charging.** `executed_actions.idempotency_key` is `UNIQUE`, and the key
is *derived* from `(record_id, attempt_number, action_type)` as a property — never
passed in, so it cannot drift from the tuple it represents. The key is claimed
*before* Razorpay is called, so a crash between the two leaves a claimed key and no
charge: payments systems may under-deliver on a retry, never double-charge.

That is the local half. The key also goes *to* Razorpay — as `receipt` on every
order and `reference_id` on every payment link, which Razorpay enforces unique —
and the retry loop looks the key up before it sends again. A request that
succeeded and timed out on the way back is found, not repeated; and if the
lookup itself fails, the wrapper refuses to re-send rather than guess.
[tests/test_idempotency_on_the_wire.py](tests/test_idempotency_on_the_wire.py)
asserts the payload, not the transcript — the distinction that let this be
false for a while without a test noticing.

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

The `dnd` row is the one to read twice. Thirty-six refusals for three records is
twelve per record — the fingerprint of a refusal with no next step, re-proposed
and re-refused on every tick. That was a bug: DND never expires, so unlike a
cooldown it never resolves itself. The guardrail now routes the record to a
person who can email, and a fresh run refuses each of those three exactly once.
The snapshot above predates the fix and still shows the loop; it is left as it
was because the headline figures were frozen from it, and because a number that
exposed a bug is worth more than one that hides it.

---

## One full case, on one screen

The jury brief asks for exactly this: one record, from the failure being detected
to the money being confirmed. `cli trace` reads it back from storage — nothing is
recomputed, so it shows what the agent recorded at the moment it decided.

```
$ python -m reclaim.cli trace REC_5001

TRACE  REC_5001  FAILED_PAYMENT · ₹8,686 · now RECOVERED

  DETECT      2026-09-02T05:15:53
              HDFC · card
              error: payment_declined — "Transaction declined. Please contact your bank."
              customer CUST_4032

  DIAGNOSE  BANK_DOWNTIME
              15 of 21 attempts on HDFC failed in this hour (71%), against a batch
              baseline of 4.6% — 15x. The error text says 'declined', but the cohort
              says the issuer is down.
              confidence 0.9 · source cohort
  DECIDE    SILENT_RETRY
              Transient issuer outage. The customer did nothing wrong and can do
              nothing about it; contacting them creates needless alarm and support load.
  GUARDRAIL ALLOWED
              All guardrails passed.
              key REC_5001:1:SILENT_RETRY
  EXECUTE   EXECUTED
              SILENT_RETRY via no channel.
              key REC_5001:1:SILENT_RETRY · ref order_stub_59349ca505b838
  OUTCOME   RECOVERED
              order.paid on order_stub_59349ca505b838 traced to intervention 1
              (SILENT_RETRY, attempt 1, policy FAILED_PAYMENT.BANK_DOWNTIME).

  WEBHOOKS    1 event(s)
              order.paid    PROCESSED    ₹8,686    modelled    order_stub_59349ca505b838

  MONEY       ₹8,686 of ₹8,686 recovered  ·  modelled outcome, real attribution chain
              0 real event(s), 1 modelled
```

The error text says *declined*. Read alone, that is a customer to message. The
cohort says fifteen of twenty-one HDFC attempts failed in the same hour, so the
agent said nothing to anyone and retried silently — zero contacts on this cause,
and the counterfactual (fifteen messages not sent) is computed, not claimed.

The last line is the one to read twice. Every webhook event carries `simulated`,
and `trace` reports it per event rather than adding real and modelled together.
`REC_5100` shows a refusal — two guardrails, a human queue row, nothing sent —
and `INV_7059` shows a promise kept. Run it on any id from `cli detect`.

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

**Razorpay has delivered here, for real.** A ₹683 payment link minted by the
executor during a live batch, paid with a test card in a browser, and delivered by
Razorpay to the deployed endpoint: `payment.failed`, then `payment.authorized`,
`payment.captured`, `order.paid`, `payment_link.paid`. All five HMAC-verified over
the raw bytes, deduplicated, and walked back link → intervention → `REC_5085`.
Every one carries `simulated: false` — nothing in that row was signed by us. Two
read `ALREADY_ATTRIBUTED` rather than `PROCESSED` because the record had already
recovered when the money arrived, which is the deduplication working rather than a
miss, and `payment.authorized` is `IGNORED` by design, because authorization is not
capture. The five are committed at [evidence/webhook.json](evidence/webhook.json):
proof on an ephemeral disk has an expiry date, proof in git does not.

**What is modelled is the batch.** That delivery landed on the deployed
instance, against a record the shipped snapshot had already settled — so the
scoreboard you run locally is built from 74 locally signed events, not from it.
[reclaim/measure/settlement.py](reclaim/measure/settlement.py) signs Razorpay-shaped payloads and
posts them through the same `receive()` a real delivery hits — nothing bypasses
the signature check or the attribution walk. The outcome simulator decides only
*whether the customer paid*. Everything it produces is stored `simulated: true`,
so real and modelled are separable at the row level and are never silently mixed.

---

## The LLM never touches money

Layer 2 runs only on the records an error string cannot resolve, using forced tool
use against a closed enum. The model cannot invent a root cause — only pick a wrong
one from a fixed list, which the policy table and the fourteen guardrails below it
still contain. A schema violation becomes `UNKNOWN` and reaches a human; it never
becomes a guess.

**Layer 2 runs live on `gemini-3.5-flash-lite`.** There is no `ANTHROPIC_API_KEY`
on this machine — the Anthropic path is implemented and tested, but every figure
below was produced by Gemini. With the model off, the batch still completes: 36 of
120 payment records fall to `UNKNOWN` on the first pass (38 by the end of the run,
as the issuer outage dissolves and its cohort evidence with it) and go to a person
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

`reclaim/` is laid out as the pipeline runs. `runner.py` walks the six stages in
order, and every stage writes to the audit log, including the stages that refuse.

```
reclaim/
  detect/        1  one plugin per leak type -> detect() -> list[AtRiskRecord]
  diagnose/      2  layer 1 lookup, layer 2 model, cohort signals; conversation/ reads replies
  decide/        3  policies.yaml + engine: a label becomes a proposed action; human_queue
  guardrails/    4  fourteen rules, one file each, and the gate that runs them over a batch
  execute/       5  Razorpay wrapper: idempotency keys, backoff, DRY_RUN, channels
  measure/       6  webhooks (raw-byte HMAC + attribution), scoreboard, settlement,
                    baseline, what-if, promises, trace, evidence, ablation
  rules/            THE single rule loader: database first, YAML fallback; validation, admin
  audit/            append-only decision log
  api/              FastAPI: /api/* for the UI, /webhooks/razorpay for Razorpay
  synthetic/        seeded leak generator, outcome simulator, Razorpay payloads
  runner.py         the batch orchestrator — detect -> ... -> measure
  enums.py          closed enums — RootCause is why hallucination is harmless
  models.py         Pydantic boundary models; AtRiskRecord stays V2-generic
  db.py             the tables + the two database-level guarantees
  clock.py          the demo clock — persisted, so ticks survive a restart
  verify.py         structural self-audit
ui/                 Next.js dashboard, static-exported and served by FastAPI
tests/              455 tests, including the guardrail property invariants
evidence/           committed proof: baseline, ablation, verify, a real webhook delivery
extras/             demo-video scripts, screenshots, handoff notes — not part of the app
```

The stages are not split by flow or by version, on purpose: the whole claim is
that a failed payment and an overdue invoice go through **the same engine**. Where
each flow and each version actually lives:

| Stage | Failed payments, carts, mandates (V1) | Invoices + promise-to-pay (V2) |
|---|---|---|
| Detect | `failed_payments.py`, `abandoned_carts.py`, `failed_mandates.py` | `overdue_invoices.py` |
| Diagnose | `deterministic.py`, `llm_diagnoser.py`, `gemini_diagnoser.py`, `cohort.py` | `receivables.py`, `conversation/` (reads replies, spots promises) |
| Decide | `policies.yaml` rows per cause | the same file, plus the `ladder:` dunning field |
| Guardrails | thirteen rules in `rules/` | `promise_window.py` — a promise is a guardrail, not a branch |
| Execute | payment links, retries, messages | the same actions, on a dunning schedule |
| Measure | `scoreboard.py`, `baseline.py`, `settlement.py`, `webhooks/` | `promises.py` (kept / broken), DSO on the scoreboard, `whatif.py` |

V2 also made the rules editable: `rules/` reads the database before the YAML,
and `rules/validation.py` refuses a bad edit whole.

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

**`ADMIN_TOKEN` locks the write surface.** Rule edits, the kill switch and resets
answer only to an `X-Admin-Token` header. With no token configured, only the
machine running the server may make them — so a laptop demo needs no setup and
a deployment that forgot the variable is locked rather than open. The dashboard
asks for the token once per tab and keeps it in `sessionStorage`; it is never in
the bundle, because a public static site cannot keep a secret. The kill switch
is persisted in the database, so a free instance restarting itself cannot
quietly re-arm the agent. [tests/test_admin_auth.py](tests/test_admin_auth.py)
asserts all of it, including that the sandbox stays open to visitors.

## Honesty note

Real Razorpay APIs, real error-code shapes, real payment links, real HMAC
verification, real attribution. **Customer response is modelled** by
[synthetic/outcomes.py](reclaim/synthetic/outcomes.py) — per-cause success
probabilities, stated openly rather than presented as live conversion data.

Three caveats, listed here rather than left for a judge to find:

1. **Layer 2's accuracy depends on a fixture we corrected.** It scored 0% on
   its 36 records until the generator was fixed to give `INSUFFICIENT_FUNDS`
   records the signals that identify one — prior payment history, a late-night
   attempt near month-end. Those are what `policies.yaml` already assumes when
   it retries at `next_salary_window`; the fixture simply had not carried them.
   The prompt was sharpened in the same pass. Both are stated because 97.5% read
   without them would be a number doing more work than it earned.
2. **Exactly one delivery has arrived from Razorpay** — five events for
   `REC_5085`, ₹683, committed at [evidence/webhook.json](evidence/webhook.json)
   and every one `simulated: false`. Every other event in the batch is a locally
   signed payload driven through the same endpoint and stored `simulated: true`.
   Stated plainly: **none of the ₹27,44,651 on the scoreboard was confirmed by
   Razorpay.** The one real delivery arrived on the deployed instance against a
   record the snapshot had already settled, so it read `ALREADY_ATTRIBUTED` and
   added nothing to the total. What it proves is the path — that a real
   Razorpay event verifies, deduplicates and walks back to the intervention that
   minted the link. The chain is the part that was hard and it is real; who paid
   is the part that is drawn.
3. **Only two error reasons were harvested from live test-mode payments**
   (`payment_cancelled`, `international_transaction_not_allowed`). The rest of
   `DETERMINISTIC_MAP` is validated against Razorpay's published error-reason
   list rather than observed on this account — every one of its 37 keys appears
   in that list, but 35 of them have not been seen arrive here. Razorpay
   Checkout fingerprints and blocks headless browsers, so the remaining
   scenarios need a human at a real browser to harvest.
