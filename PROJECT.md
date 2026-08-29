# ReclaimAI — AI Revenue Recovery Agent

**Razorpay Buildathon — Track 03: AI Revenue Recovery**
Solo build. 4 days. Full V1 scope, no cuts.

---

## 1. The track brief (the bar we must clear)

> Build an agent that detects revenue at risk, determines the right intervention, and executes
> a bounded recovery workflow: from payment failures and checkout abandonment to overdue receivables.
>
> **The bar:** Don't just identify the problem. Show measured money recovered across a batch,
> with compliant escalation, stopping rules, and an audit trail.

Five things the demo MUST show:

| Requirement | Concrete proof |
|---|---|
| Measured money recovered | "₹8.25L at risk → ₹1.22L recovered (36.7% of records)" across 120 records |
| Across a batch | Bulk run, not one cherry-picked transaction |
| Compliant escalation | Gentle → firmer → human handoff; quiet hours; opt-out honoured |
| Stopping rules | Max attempts, cooldowns, permanent stop on opt-out |
| Audit trail | Every decision with reason, timestamp, outcome — replayable |

**Differentiator:** most teams build a "failed payment notifier" (step 1 of 5). We ship
diagnosis → bounded decisioning → guardrails → honest measurement.

---

## 2. What we build

An agent that recovers revenue for a Razorpay merchant across **three leak types**:

1. **Failed payments** — payment attempted, bank/card declined
2. **Abandoned checkouts** — order created, never paid
3. **Failed subscription mandates** — recurring auto-debit bounced

### The loop

```
DETECT → DIAGNOSE → DECIDE → GUARDRAIL → EXECUTE → MEASURE
  ↑                                                    │
  └──────────── learn / stop / escalate ───────────────┘
```

| Stage | Owner | Nature |
|---|---|---|
| DETECT | Razorpay API pollers | deterministic |
| DIAGNOSE | deterministic map + LLM fallback | fuzzy |
| DECIDE | policy table (YAML) | deterministic |
| GUARDRAIL | 13-rule permission gate | deterministic |
| EXECUTE | Razorpay Payment Links / retries | deterministic |
| MEASURE | webhook attribution + scoreboard | deterministic |

### THE CORE DESIGN RULE

**The LLM never touches money.** It produces only a *label* from a closed enum.
A deterministic table turns that label into a proposed action. A deterministic gate
decides whether the action may fire.

When asked "what if the model hallucinates?": *it can only hallucinate a category
from a fixed enum, and the guardrails still hold.*

---

## 3. Architecture

```
detectors/          → produce AtRiskRecord (plugin per leak type)
brain/
  diagnosis/        → error+context → RootCause (deterministic layer, then LLM)
  policy/           → RootCause → ProposedAction (policies.yaml)
  guardrails/       → ProposedAction → ALLOW | BLOCK(reason, deferred_until)
executor/           → fires allowed actions against Razorpay
webhooks/           → receives outcomes, attributes recovery
audit/              → immutable decision log
api/                → FastAPI routes for UI
ui/                 → Next.js dashboard
```

### Generic record shape (V2-ready — do NOT make this payment-specific)

```python
AtRiskRecord:
    id: str
    leak_type: LeakType          # FAILED_PAYMENT | ABANDONED_CART |
                                 # FAILED_MANDATE | OVERDUE_INVOICE (V2)
    amount: int                  # paise
    currency: str
    counterparty_id: str         # customer id
    source_ref: str              # razorpay payment/order/subscription id
    detected_at: datetime
    due_at: datetime | None
    raw_signals: dict            # error codes, invoice age, method, issuer...
    state: RecordState           # AT_RISK | IN_PROGRESS | RECOVERED |
                                 # UNRECOVERABLE | ESCALATED | CLOSED
    attempts: int
    next_action_at: datetime | None
```

### Extension points (so V2 is hours, not a rewrite)

1. **Detectors are plugins** — `detectors/*.py` each expose `detect() -> list[AtRiskRecord]`
2. **Policy is data** — `policies.yaml` keyed by `leak_type` then `root_cause`
3. **Channels are abstracted** — `send(channel, recipient, message)`;
   guardrails live ABOVE the channel so a new channel (voice) inherits them free
4. **Rules load through one module** (`brain/rules.py`) — V2 swaps the loader for DB

---

## 4. Root cause taxonomy (CLOSED ENUM — this makes hallucination harmless)

```python
class RootCause(str, Enum):
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"   # timing problem — retry later
    BANK_DOWNTIME      = "BANK_DOWNTIME"        # transient — retry soon, stay silent
    EXPIRED_INSTRUMENT = "EXPIRED_INSTRUMENT"   # needs new method
    INVALID_INSTRUMENT = "INVALID_INSTRUMENT"   # needs new method
    AUTH_DROPOFF       = "AUTH_DROPOFF"         # user abandoned OTP/3DS
    LIMIT_EXCEEDED     = "LIMIT_EXCEEDED"       # daily/txn cap — retry tomorrow
    RISK_DECLINE       = "RISK_DECLINE"         # issuer suspects fraud — NEVER retry
    POLICY_BLOCK       = "POLICY_BLOCK"         # intl/blocked — human
    CART_ABANDONMENT   = "CART_ABANDONMENT"     # never attempted payment
    MANDATE_REVOKED    = "MANDATE_REVOKED"      # subscription killed — NEVER retry
    TECHNICAL_ERROR    = "TECHNICAL_ERROR"      # our side / integration
    UNKNOWN            = "UNKNOWN"              # explicit escape hatch → human
```

`UNKNOWN` is deliberate. A model forced to always answer confidently will fabricate.

---

## 5. Diagnosis engine — two layers

### Layer 1: deterministic map (~60% of records, free, confidence 1.0)

```python
DETERMINISTIC_MAP = {
    "GATEWAY_ERROR":                          RootCause.BANK_DOWNTIME,
    "card_expired":                           RootCause.EXPIRED_INSTRUMENT,
    "invalid_card_number":                    RootCause.INVALID_INSTRUMENT,
    "international_transaction_not_allowed":  RootCause.POLICY_BLOCK,
    "payment_pending":                        RootCause.AUTH_DROPOFF,
    # ... extend as we see real test-mode codes
}
```

### Layer 2: LLM for the ambiguous rest (~40%)

Razorpay's generic `BAD_REQUEST_ERROR / payment_failed / "declined by the bank"` covers
insufficient funds, card blocked, risk decline, and daily-limit — four causes needing four
different responses. That is where the model earns its place.

**Model:** `claude-sonnet-5`, or `gemini-3.5-flash-lite` when only a Gemini key is
present. Whichever answers, the contract above it is identical: a label from a
closed enum and a confidence, never an action.
**Method:** forced tool use for structured output — `tool_choice` on Anthropic,
`function_calling_config` mode `ANY` on Gemini.

```python
DIAGNOSIS_TOOL = {
  "name": "record_diagnosis",
  "input_schema": {
    "type": "object",
    "properties": {
      "root_cause":    {"type": "string", "enum": [c.value for c in RootCause]},
      "confidence":    {"type": "number", "minimum": 0, "maximum": 1},
      "reasoning":     {"type": "string"},
      "recoverable":   {"type": "boolean"},
      "evidence_used": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["root_cause","confidence","reasoning","recoverable","evidence_used"]
  }
}
# tool_choice={"type":"tool","name":"record_diagnosis"}  ← forces the schema back
```

Validate with Pydantic. **On validation failure → UNKNOWN → human. Never crash the batch.**

### Context fed to the model

```python
{
  "amount": 12400, "currency": "INR", "method": "card",
  "card_network": "VISA", "card_type": "debit", "issuer_bank": "HDFC",
  "error": {"code","description","source","step","reason"},
  "attempt_number": 1, "attempted_at": "...",
  "customer_history": {
      "successful_payments_lifetime": 4,
      "last_successful_at": "...",
      "failed_payments_last_30d": 2,
      "same_instrument_succeeded_before": true
  },
  "cohort_signal": {
      "same_issuer_failure_rate_last_1h": 0.71,   # ← GOLD
      "baseline_failure_rate": 0.04
  }
}
```

**Two signals do enormous work:**

- `same_instrument_succeeded_before: true` → card is fine → funds/limits, not a bad card
- `same_issuer_failure_rate_last_1h: 0.71` → **bank outage, not customer problem** →
  retry in 20 min and tell the customer NOTHING

That second one is a headline feature: detecting "this is not the customer, this is the bank"
and staying silent instead of SMS-blasting 400 people is exactly the restraint the bar rewards.
Compute it by grouping the batch on (issuer, time bucket) BEFORE diagnosis.

### Prompt rules (encode domain knowledge)

```
You are a payments failure analyst for an Indian merchant on Razorpay.
Classify ONE failed payment into exactly one root cause.

- Choose UNKNOWN if evidence genuinely does not distinguish. Do not guess to appear confident.
- If cohort failure rate for the issuer is far above baseline, prefer BANK_DOWNTIME
  even if the error text says "declined".
- If the same instrument succeeded before, do NOT choose INVALID_INSTRUMENT or
  EXPIRED_INSTRUMENT unless the error explicitly says so.
- RISK_DECLINE and MANDATE_REVOKED are never recoverable by retry.
- Late-night attempts near month-end with a previously-good instrument weakly
  suggest INSUFFICIENT_FUNDS.
- confidence < 0.6 routes to human review. Use it honestly.
```

### Cost/latency controls

- **Cache by signature** — hash `(error_code, reason, method, issuer, attempt_number)`
- **Concurrency** — semaphore of ~8
- **Always fall back** — API down → deterministic map → UNKNOWN → human queue.
  Demo this: killing the LLM mid-run and degrading gracefully is the "one failure
  handled gracefully" beat.

---

## 6. Policy table (data, not code — `brain/policy/policies.yaml`)

```yaml
FAILED_PAYMENT:
  BANK_DOWNTIME:
    strategy: silent_retry            # NO customer contact at all
    schedule: [20m, 2h, 24h]
    max_attempts: 3
    notify_customer: false
    rationale: "Transient issuer outage; contacting the customer creates
                needless alarm and support load."

  INSUFFICIENT_FUNDS:
    strategy: scheduled_retry
    schedule: [next_salary_window, +7d]
    max_attempts: 2
    notify_customer: true
    channel: whatsapp
    tone: gentle
    rationale: "Timing problem, not intent. Retry when balance likely restored."

  EXPIRED_INSTRUMENT:
    strategy: request_new_method
    schedule: [immediate, +48h]
    max_attempts: 2
    notify_customer: true
    channel: email
    payment_link: {prefill_method: upi}   # do not send them back to the dead card
    rationale: "Retry cannot succeed; customer action required."

  AUTH_DROPOFF:
    strategy: friction_reduction
    schedule: [15m, +24h]
    max_attempts: 2
    notify_customer: true
    channel: sms
    payment_link: {prefill_method: upi}   # UPI removes the OTP step
    rationale: "User abandoned 3DS/OTP."

  LIMIT_EXCEEDED:
    strategy: scheduled_retry
    schedule: ["+1d@10:00"]
    max_attempts: 1
    notify_customer: false

  RISK_DECLINE:    {strategy: no_auto_action, escalate_to: human,
                    rationale: "Issuer flagged risk. Retries look like card testing."}
  MANDATE_REVOKED: {strategy: no_auto_action, escalate_to: human}
  POLICY_BLOCK:    {strategy: no_auto_action, escalate_to: human}
  UNKNOWN:         {strategy: no_auto_action, escalate_to: human,
                    rationale: "Insufficient confidence to act on someone's money."}
```

**Five rows are `no_auto_action`. That is intentional and it is the differentiator.**
Most teams build something that always acts. Knowing five cases where the right move is
stop-and-get-a-human is what looks production-ready.

`next_salary_window` → next 1st–3rd of month at ~11:00 IST. Indian salary cycles cluster
there. Measurable: *"salary-timed retries recovered 41% vs 12% for immediate retry."*

### Policy output — PROPOSED, not executed

```python
ProposedAction:
    record_id: str
    action_type: ActionType      # RETRY | SILENT_RETRY | SEND_LINK | NOTIFY |
                                 # ESCALATE | NO_ACTION
    channel: Channel | None
    scheduled_for: datetime
    attempt_number: int
    policy_ref: str              # "FAILED_PAYMENT.BANK_DOWNTIME" ← traceability
    rationale: str
    amount: int
```

---

## 7. Guardrail engine (13 rules — deterministic, dumb, separate)

Knows nothing about root causes. Answers only: **may this action fire, right now,
against this customer?**

```python
def evaluate_all(action, ctx) -> GuardrailResult:
    """Runs ALL guardrails, collects ALL violations. Never raises. Never calls an LLM."""
```

| # | Guardrail | Rule | Why it matters |
|---|---|---|---|
| 1 | Kill switch | `AUTOPILOT_ENABLED=false` → block everything | the panic button |
| 2 | Consent / opt-out | opted out → block all contact forever | legal, non-negotiable |
| 3 | DND registry | on DND → no SMS/voice; email/link only | TRAI compliance |
| 4 | Quiet hours | contact only 09:00–20:00 IST (silent retries exempt) | collections norms |
| 5 | Max attempts | ≤ policy max AND ≤ 3 global hard cap | prevents harassment |
| 6 | Cooldown | min gap since last contact (24h) | prevents burst spam |
| 7 | **Frequency cap** | ≤ 2 contacts / 7 days / **customer**, across ALL records | the one teams forget |
| 8 | Value ceiling | amount > ₹50,000 → human approval | bounded authority |
| 9 | Daily budget | ≤ N auto-actions / merchant / day | blast radius |
| 10 | **Idempotency** | `(record_id, attempt_n, action_type)` key unused | **NO DOUBLE-CHARGING** |
| 11 | State validity | record still AT_RISK, not already recovered | do not chase paid invoices |
| 12 | Confidence floor | diagnosis confidence < 0.6 → human | ties LLM honesty to behaviour |
| 13 | Freshness | record older than 90 days → close | stopping rule |

**#7 and #10 separate us from the pack.** Frequency cap requires thinking at the
*customer* level, not the record level — a customer with 4 failed payments must get
2 messages, not 4. Idempotency is table stakes in payments and most hackathon
projects do not have it.

### Blocked ≠ dropped

```python
GuardrailResult(allowed, violations, deferred_until, requires_human)
```

- quiet hours → reschedule 09:00
- cooldown → defer to cooldown end
- consent → close permanently
- value ceiling → route to human queue

UI state: *"deferred — will retry 09:00 tomorrow (quiet hours)."*

### Log every block

```python
audit.log(record_id=..., stage="GUARDRAIL", outcome="BLOCKED",
          guardrail="frequency_cap",
          reason="Customer CUST_8821 already received 2 contacts in last 7 days",
          proposed_action=action, deferred_until="2026-08-29T09:00:00+05:30")
```

Demo line: **"The agent wanted to take 167 actions. It was allowed 120.
Here is every one it was not allowed to take, and why."** That is the winning moment.

### Guardrail tests (judges WILL ask)

```python
test_opted_out_customer_never_contacted()
test_silent_retry_allowed_at_3am_but_sms_is_not()
test_idempotency_blocks_replay()
test_frequency_cap_spans_multiple_records()      # 4 records → exactly 2 messages
test_high_value_requires_human()
test_risk_decline_never_produces_an_action()     # policy-level
test_kill_switch_stops_everything()
test_guardrails_never_raise()                    # fuzz 500 malformed → block, not crash
```

Plus **property tests** over random batches asserting two invariants:
1. no customer ever receives > 2 contacts in 7 days
2. no `(record, attempt, action_type)` executes twice

**Fail closed, always.** A guardrail that throws is a guardrail someone wraps in
`except: pass` and silently skips.

---

## 8. Execution

- **Payment Links** — `POST /payment_links` — the primary recovery channel
- **Retry** — fresh order / subscription retry
- **Method switching** — link prefilled to UPI when cards failed
- **Notifications** — Razorpay link SMS/email in test mode
- **Human queue** — escalations go to a review table, not the void
- **Idempotency keys on every write**

### Webhooks (this is how the loop closes)

`payment.failed`, `payment.captured`, `payment_link.paid`, `subscription.charged`,
`order.paid` — verify signature (HMAC-SHA256 with webhook secret), then attribute:
link paid → mark record RECOVERED → attribute ₹ to the intervention that created it.

**Without webhooks you are polling and guessing. That attribution chain is the proof.**

---

## 9. Measurement — the scoreboard (this is what wins)

```
BATCH RESULTS (n = 120 at-risk records)
─────────────────────────────────────────
Money at risk               ₹ 5,84,300
Money recovered             ₹ 2,03,150   (34.8%)
Still open                  ₹ 1,12,000
Written off / unrecoverable ₹ 2,69,150

Recovery rate by root cause:
  Bank downtime          78%   ← retries just work
  Insufficient funds     41%   ← timing matters
  3DS drop-off           29%   ← UPI switch helps
  Card expired           18%
  Policy block            0%   ← correctly escalated, not chased

Guardrails fired: 47
  quiet hours 19 | max attempts 12 | opted out 9 | value ceiling 7
Human escalations: 6
Messages sent per recovered payment: 1.4
```

### Baseline comparison — MANDATORY

Run a naive strategy (retry everything 3x immediately, message every failure) over the
same batch:

> "Naive: 19% recovered, 3.1 contacts per recovery.
>  Ours: 35% recovered, 1.4 contacts per recovery."

**35% alone means nothing. 35% vs 19% means everything.**

---

## 10. Data strategy

Synthetic leak generator creating ~120 realistic at-risk records in the Razorpay
**test account**:

- Use Razorpay test cards that force specific failures → **real error codes**
- Create orders never paid → real abandoned checkouts
- Cause distribution: ~30% insufficient funds, ~20% bank downtime, ~15% 3DS drop,
  ~15% expired card, ~15% abandonment, ~5% policy blocks
- Amounts ₹199 – ₹85,000 so the value-ceiling guardrail actually fires
- ~20% of customers have prior successful payments (affects diagnosis confidence)
- ~8% carry an opt-out flag
- Cluster some failures on one issuer in one hour → triggers the cohort/outage signal

**Customer response is simulated** via per-cause probability
(bank-downtime retries succeed ~75%, expired-card links convert ~18%, etc.)
so the batch produces honest, varied numbers.

⚠️ **Say this openly in the demo:** "Real Razorpay APIs, real error codes, real payment
links; customer response modelled." Honesty is in the judging bar — claiming 90%
recovery makes judges suspicious; 35% with a clear unrecoverable list makes you credible.

---

## 11. Tech stack

**Backend** — Python 3.11+ / FastAPI
- `razorpay` official SDK
- `anthropic` SDK → `claude-sonnet-5`, or `google-genai` → `gemini-3.5-flash-lite`
  (free tier). Layer 2 is one `Callable[[AtRiskRecord, CohortSignal | None],
  Diagnosis | None]`, so the provider is a config value, not an architecture.
- SQLite (zero setup) via SQLAlchemy
- APScheduler for the retry queue (plus a manual `/tick` endpoint for demo control)
- Pydantic for all boundary validation
- pytest + hypothesis (property tests)

**Frontend** — Next.js + Tailwind + shadcn/ui — three screens:
1. **Dashboard** — the scoreboard, big numbers, recovery-by-cause chart
2. **Recovery queue** — at-risk records, state, next action, next action time
3. **Audit trail** — click a record → full decision timeline

**Razorpay APIs** — Orders, Payments, Payment Links, Subscriptions, Webhooks.
Test mode throughout (`rzp_test_...`).

---

## 12. V1 / V2 split

### V1 (the four days) — everything above
Three leak types, both diagnosis layers, full 13 guardrails + tests, execution,
webhooks, dashboard, audit trail, baseline comparison.

Rules were **static YAML loaded at startup**. Not admin-editable.

---

### V2 — what shipped

All of (a) and (b) below are built and tested. (c) remains a slide, deliberately,
and §12c explains why it is a better slide now than it was.

**a) Dynamic rule configuration — SHIPPED**
- `policy_rules`, `guardrail_config`, `rule_change_log` in `db.py`.
  `rule_change_log` carries the same append-only triggers as `audit_log`.
- `brain/rules.py` reads the database first and falls back to the YAML, which
  remains the default and the reset target. **`policies()`, `guardrail_config()`,
  `policy_for()` and `threshold()` kept their exact signatures, so not one call
  site in diagnosis, policy or the guardrails changed.** That was V1's bet and
  this is where it paid.
- Hot reload is a generation counter bumped on write, not an mtime watch: there
  is no window in which half a batch reads the old ceiling and half the new one.
- Admin API: `GET /api/admin/rules`, `POST /api/admin/policy/{leak}/{cause}`,
  `POST /api/admin/guardrail/{name}`, `GET /api/admin/changes`,
  `POST /api/admin/reset`, `POST /api/admin/replay`.
- `brain/validation.py` is the load-bearing part. A merchant must not be able to
  type a rule that kills tonight's batch, so every edit is validated by REUSING
  the code that will consume it — a schedule token is valid if `schedule.resolve`
  parses it, a strategy if `STRATEGY_TO_ACTION` maps it. Unknown keys are refused
  rather than stored and never read. Bounds are enforced on thresholds, because
  a frequency cap of 500 parses perfectly and is a licence to spam.
- **What-if replay** (`whatif.py`) — not in the original plan, and the reason the
  studio is worth having. Change a threshold and replay the SAME batch under both
  rule sets, with the difference in rupees, messages and human escalations.
  Side-effect-free by construction: it runs the real runner, gate, executor and
  settlement against a scratch SQLite file that is then deleted. Diagnoses are
  frozen from the audit log, because rules affect DECIDE/GUARDRAIL/EXECUTE and
  never DIAGNOSE — which is what makes it free AND what makes the comparison
  valid. `cli verify` asserts the live row counts and the demo clock are
  unchanged across a replay.

**b) B2B receivables + promise-to-pay — SHIPPED**
- `detectors/overdue_invoices.py` is the payments detector with one enum member
  changed. No schema migration; `AtRiskRecord` stayed generic exactly as §3 said
  it would.
- Five new causes: `INVOICE_NOT_RECEIVED`, `INVOICE_DISPUTED`, `AWAITING_APPROVAL`,
  `BUYER_CASH_CRUNCH`, `PAYMENT_STALLED`.
- **A layer 1 for receivables** (`diagnosis/receivables.py`). Invoices have no
  error string, which looks like a reason to send them all to the model and is
  not: a dispute flag is a person's recorded decision, a partial payment either
  arrived or did not, and whether an invoice is late by the buyer's OWN average
  is arithmetic. It resolves ~48% of the book at 100% accuracy and defers the
  genuinely ambiguous pair — an invoice nobody received and one that has simply
  stalled look identical from the ledger.
- Dunning ladder as data. `ladder:` is the one new policy field: day 1 polite,
  day 7 firmer with a link, day 15 with the finance manager copied, day 30 a
  human account manager while the agent stops.
- **Promise-to-pay is a GUARDRAIL, not a branch in the runner.** Guardrail 14,
  `promise_window`, blocks contact on a record with an open, unexpired promise.
  Expressed there it answers the same question every other guardrail answers, it
  fails closed, it lands in the audit trail as a block with a readable reason, it
  appears in the guardrail breakdown next to quiet hours, and any channel added
  later — including voice — inherits it without knowing it exists.
- Broken promise → `PAYMENT_STALLED` → the ladder climbs a rung. A promise is
  KEPT only if the record actually recovered; taking the customer's word for it
  is how a scoreboard starts counting sentences as rupees.
- DSO on the scoreboard, value-weighted, alongside promises made/kept/broken.

**The conversation layer** — `brain/conversation/`. The model gained a second
job and it is the same job as the first: turn a sentence into one label from a
closed enum (`ReplyIntent`, seven members) with an honest confidence. A
deterministic table in `handler.py` decides what the label MEANS, exactly as
`policy/` does for `RootCause`. Five of the seven intents route to a person — a
partial-payment offer is a commercial negotiation, a dispute is a conversation
about what is owed, and "already paid" is a reconciliation.

The one field worth stating out loud: the model may extract a DATE from
"we'll pay by Friday", because that is what a language model is for. It is not
trusted with that date. `promises.validate_date` refuses anything in the past,
anything past the configured horizon and anything unparseable, and a refused date
becomes a reply a human reads rather than a record the agent puts to sleep.
**That is the rule about money, applied to time** — and in receivables, time is
how the money gets away.

**c) Hinglish voice — STILL A SLIDE, and now a better one**

Not built, for the reasons the original entry gives: telephony + STT/TTS +
turn-taking + heavy DND/TRAI/consent load + the highest live-demo risk in the
project.

But the "cheap middle ground" the original entry described — the conversation
policy and promise extraction over text — is exactly what shipped above, and it
changes what the slide can honestly claim. The hard part of a Hinglish voice
agent is understanding "sir friday tak ho jayega" and knowing that it is a
commitment, that it needs a date, and that the date must be validated before
anything acts on it. That part exists, is tested, and runs on the replies the
fixture actually produces. What is missing is audio.

Say: *"the channel layer is abstracted and the conversation policy is built. Voice
is a new channel implementation over an intent extractor that already works, and
the guardrails — DND, call windows, consent, and now the promise window — already
apply to it."*

---

### What V1 actually shipped, and where it differs from the plan above

Recorded here rather than quietly absorbed, because a spec that silently drifts
from the build is worse than one that admits the drift.

**Webhooks are real, and so was the delivery — briefly.** No tunnel was ever
needed: deploying the API to Render gave Razorpay a public URL, and five genuine
deliveries arrived and were verified — `payment.captured` PROCESSED, `order.paid`
and `payment_link.paid` ALREADY_ATTRIBUTED. Render's free tier has no persistent
disk; the instance restarted and took `/tmp` with it, so the artifact is gone
while the fact stands. `GET /api/webhooks` now returns only `simulated: true`
events from the boot seed.

The receiver, the HMAC-SHA256 verification and the attribution chain are
production code carrying 19 tests. What stands in for a delivery day to day is
`reclaim/settlement.py`: it signs Razorpay-shaped payloads and posts them through
the same `receive()` a real delivery hits, so nothing bypasses the signature check
or the walk from `payment_link.paid` back to the intervention that minted the
link. The outcome simulator decides only *whether the customer paid* — the
judgement §10 already discloses as modelled.

To have live proof standing at demo time, capture it into the repo rather than
plan to re-do it on stage: pay one link, save the `simulated: false` JSON. Proof
on an ephemeral disk is proof with an expiry date.

**Layer 2 runs on a free tier, and the fixture had to be fixed before it could.**
`gemini-3.5-flash-lite` via `google-genai`, chosen over Anthropic only because it
costs nothing; `LLMDiagnoser` and `GeminiDiagnoser` share one prompt, one closed
schema and one `CachedDiagnoser`, so the provider is a config value. Its first
live run scored 0% on the 36 records it exists to resolve — the fixture had been
labelling records `INSUFFICIENT_FUNDS` while giving them no payment history and a
midday timestamp, so UNKNOWN was the only honest answer available. Fixing the data
(and deriving `attempted_hour_ist` / `days_to_month_end` rather than making the
model parse an ISO string) took overall accuracy from 70.0% to 97.5%.

Two things that fixture work taught, both now in the code: a 429 degrades to
UNKNOWN and is therefore *indistinguishable in the scoreboard from the model
honestly declining*, so layer 2 paces itself and logs when it gives up; and the
boot seed on the API host must pass its diagnoser explicitly, or a deployment
with a key configured still publishes 38 UNKNOWNs and looks fine doing it.

**Schedules anchor on the record, not on the clock.** `20m` means twenty minutes
after the failure, not twenty minutes after whenever the batch happens to run.
Anchoring on the wall clock makes every schedule permanently twenty minutes away
and nothing ever comes due; the bug is invisible because the batch still looks
busy. Attempt 1 counts from `detected_at`, attempt N from attempt N-1 — which is
what a dunning ladder is. An action that is not yet due is parked on the record
and picked up by a later tick.

**A demo clock, persisted.** `reclaim/clock.py` adds an offset to the wall clock
and stores it, so `cli tick --advance next_salary_window` survives across separate
processes. Salary-window retries are a month out and 48-hour follow-ups are two
days out; neither is watchable otherwise. Nothing outside a demo reads it, and
`clock.reset()` puts it back.

**`GuardrailViolation` gained `closes_record`.** `permanent` already meant "never
reschedule THIS ACTION" — which is true of an idempotency block, where the record
itself is alive and moving to its next attempt. Closing a record needs a separate
flag, or every record gets killed the moment it successfully does anything. Set
by consent, freshness and max-attempts; not by idempotency or state validity.

**shadcn/ui was not installed.** The entire UI surface is a card, a badge, a stat,
a bar and a table. Four hand-written components in the same visual idiom cost less
than the component library's install and config, and the dashboard is one page
with client-side tabs rather than three routes — a static export with no
navigation cannot 404 on stage.

**Two guardrail counts, not one.** The scoreboard reports both refusals and the
distinct records held back. Over a dozen ticks the same deferral is re-evaluated
repeatedly, so "cooldown: 95" and "cooldown: 17 records" are both true and only
one of them is the number to quote. Conflating them would inflate exactly the
figure this project argues against inflating.

**Layer 2 runs live, on Gemini's free tier** (`gemini-3.5-flash-lite`). Overall
diagnosis accuracy is 97.5%: layer 1 resolves 69 records at 100%, the cohort
signal 15 at 100%, and layer 2 the remaining 36 at 91.7%.

It did not start there. The first live run scored 0% on those 36, and the reason
is worth keeping: the fixture labelled 33 records `INSUFFICIENT_FUNDS` while
drawing their customers at random, so a third of them had no payment history and
a midday timestamp. Nothing in the context distinguished them, and the model
answered UNKNOWN every time — correctly. The data, not the model, was wrong, and
it contradicted the policy acting on it: `next_salary_window` retries assume
someone who normally pays and is short until payday.

`--no-llm` remains a real code path with tests asserting the batch completes with
the model down, and the fallback chain is exercised rather than theoretical: a
dead model id and a rate limit were both caught in testing and degraded to
UNKNOWN without failing a batch.

What layer 2 still will not do is guess. The 3 `RISK_DECLINE` records carry no
tell by design; two come back UNKNOWN and reach a human, and the third is called
`INSUFFICIENT_FUNDS` at high confidence because it happens to carry that
signature. That last one is a real miss, it is in the audit trail, and it is the
honest answer to "what does your model get wrong".

---

## 13. Demo script (5 minutes)

1. **The leak** (30s) — "This merchant has ₹8.2L in failed payments and dead carts.
   Today nobody chases it."
2. **Run the batch live** (60s) — hit Run, watch records flow diagnose → decide → execute.
3. **Zoom into one record** (90s) — "Payment #4471, ₹12,400, failed. The agent read the
   error and diagnosed *insufficient funds*, not a broken card — here is its reasoning and
   the evidence it used. So it did NOT retry immediately; it scheduled for the 1st.
   It retried. It worked." Show the audit trail.
4. **Show a BLOCKED action** (45s) — "This one the agent wanted to chase. Guardrail stopped
   it: customer opted out. Two more were above the ₹50,000 ceiling, routed to a human."
   ← **THE WINNING MOMENT. Restraint reads as maturity.**
5. **The scoreboard** (60s) — recovered vs naive baseline, contacts per recovery,
   honest unrecoverable list.
6. **One graceful failure** (30s) — kill the Razorpay call: it retries, backs off, and
   parks the record for human review rather than crashing or double-charging.

### Lines to say out loud
- "Every action is idempotency-keyed — the agent can never double-charge, even if it
  crashes mid-batch."
- "We do not burn an LLM call where a lookup table answers — the model runs on the 40%
  that are genuinely ambiguous."
- "The model cannot move money. It only produces a label."

---

## 14. What separates 1st from 10th

1. **Root-cause diagnosis, not just detection** — anyone lists failed payments;
   explaining *why* and acting differently per why is the product
2. **Restraint on stage** — show the agent choosing NOT to act
3. **A baseline** — 35% vs 19% naive
4. **Idempotency** — payments judges care about this more than anything
5. **An honest unrecoverable list** — credibility beats inflated numbers
  