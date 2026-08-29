# PLAN — 4-Day Solo Build

Read `PROJECT.md` first for the spec. This file is the schedule and the checklist.

**Budget:** ~40 hours over 4 days ≈ 10h/day, solo.
**Rule:** do not gold-plate Days 1–2. The slack you keep is what saves Day 4.

**Progress:** update the checkboxes in this file as you go. A new chat should read
`PROJECT.md` + `PLAN.md`, find the first unchecked box, and continue from there.

---

## Status

- [x] Day 1 — Foundation
- [x] Day 2 — Brain
- [x] Day 3 — Hands
- [x] Day 4 — Face + Proof
- [x] Day 5 — V2: receivables, promises, rules studio

---

## Day 1 — Foundation (~9h)

**Done when:** 120 synthetic at-risk records sit in the DB, created through real
Razorpay test-mode API calls with real error codes, and `pytest` runs green on the
record/schema layer.

- [x] **1.1 Repo skeleton** (0.5h)
  - `pyproject.toml` / `requirements.txt`, `.env.example`, `.gitignore`
  - Package layout per PROJECT.md §3
  - `git init` + first commit
- [x] **1.2 Razorpay test account** (0.5h)
  - Generate `rzp_test_` key id + secret → `.env` (NEVER commit)
  - Smoke test: create an order, fetch it back
  - Note the exact error-code strings test cards return — feed them into
    `DETERMINISTIC_MAP`
  - Two reasons harvested live via `cli harvest --collect`. The rest were
    verified against Razorpay's published error-reason list, which caught 16
    map keys and 3 generator strings that Razorpay never emits — they could
    never have matched in production. Automating the remaining scenarios is
    not possible: Checkout runs Sardine + invisible hCaptcha + HumanSecurity
    and refuses a headless browser.
- [x] **1.3 Domain models** (1h)
  - `AtRiskRecord`, `LeakType`, `RecordState`, `RootCause`, `ActionType`, `Channel`
  - Pydantic models + SQLAlchemy tables
  - Keep `AtRiskRecord` GENERIC (see PROJECT.md §3) — no payment-specific fields
- [x] **1.4 DB schema + SQLite** (1h)
  - Tables: `at_risk_records`, `customers`, `interventions`, `executed_actions`
    (idempotency keys), `audit_log`, `human_queue`
  - `audit_log` is append-only — no updates, no deletes
- [x] **1.5 Razorpay client wrapper** (1h)
  - Thin wrapper with retry + backoff + timeout
  - Idempotency-key support on every write call
  - A `DRY_RUN` flag that logs instead of calling (invaluable for testing)
- [x] **1.6 Synthetic leak generator** (3h) ← the big one
  - Create ~120 records: ~30% insufficient funds, ~20% bank downtime, ~15% 3DS drop,
    ~15% expired card, ~15% abandonment, ~5% policy block
  - Amounts ₹199–₹85,000 (so the value ceiling actually fires)
  - ~20% customers with prior successful payments; ~8% opted out; some on DND
  - **Cluster ~15 failures on one issuer inside one hour** → makes the cohort/outage
    signal real
  - Also write the **outcome simulator**: per-cause success probabilities
    (bank downtime retry ~75%, insufficient funds on salary day ~41%,
    expired-card link ~18%, 3DS→UPI ~29%, risk decline 0%)
- [x] **1.7 Detectors** (1.5h)
  - `detectors/failed_payments.py`, `abandoned_carts.py`, `failed_mandates.py`
  - Each exposes `detect() -> list[AtRiskRecord]`; register in a list
  - Verify: running all detectors returns 120 records with a correct total ₹ at risk
- [x] **1.8 Audit log module** (0.5h)
  - `audit.log(record_id, stage, outcome, reason, payload, ...)` — append-only

**Day 1 checkpoint:** `python -m reclaim.cli detect` prints
`120 at-risk records, ₹8,24,984 at risk`.

---

## Day 2 — Brain (~10h)

**Done when:** the full guardrail test suite is green and a record can go
error → RootCause → ProposedAction → ALLOW/BLOCK without touching Razorpay.

- [x] **2.1 Deterministic diagnosis map** (1h)
  - `brain/diagnosis/deterministic.py` using the REAL codes seen on Day 1
  - Target: resolves ~60% of records at confidence 1.0
- [x] **2.2 Cohort signal** (1h)
  - Group batch by (issuer, hour bucket), compute failure rate vs baseline
  - This is what turns "declined" into BANK_DOWNTIME → silent retry
- [x] **2.3 LLM diagnosis** (2.5h)
  - Forced tool use (`record_diagnosis`), `claude-sonnet-5`
  - Pydantic validation; validation failure → UNKNOWN → human
  - Signature cache + semaphore(8)
  - Fallback chain: LLM → deterministic → UNKNOWN. **Never crash the batch.**
  - **Runs live on `gemini-3.5-flash-lite`** (free tier) as well as Anthropic.
    Both diagnosers share one prompt, one closed schema and one
    `CachedDiagnoser`; the provider is a config value. Anthropic wins when both
    keys are set, because that is what PROJECT.md describes.
  - The fallback chain is exercised, not theoretical: a retired model id (404)
    and a daily-quota 429 were both hit in testing and both degraded to UNKNOWN
    without failing a batch. Because a 429 is indistinguishable from an honest
    refusal in the scoreboard, layer 2 paces itself under the free-tier limit
    and logs when it gives up.
- [x] **2.4 Policy engine** (1.5h)
  - `policies.yaml` per PROJECT.md §6, loader in `brain/rules.py` (ONE loader —
    V2 swaps this for DB)
  - `decide(record, diagnosis) -> ProposedAction` with `policy_ref` set
  - Schedule resolver: `20m`, `+7d`, `+1d@10:00`, `next_salary_window`, `immediate`
- [x] **2.5 Guardrail engine** (2.5h)
  - `brain/guardrails/base.py` — Protocol, Verdict, `evaluate_all` collecting ALL
    violations, never raising
  - All 13 rules, one file each in `rules/`
  - `GuardrailResult(allowed, violations, deferred_until, requires_human)`
  - Blocked ≠ dropped: compute `deferred_until` per violation
- [x] **2.6 Guardrail tests** (1.5h) ← DO NOT SKIP, judges will ask
  - The 8 named tests in PROJECT.md §7
  - 2 property tests (hypothesis): frequency-cap invariant, idempotency invariant
  - `pytest tests/test_guardrails.py` must be green before Day 3

**Day 2 checkpoint:** `pytest` all green; `python -m reclaim.cli plan` prints proposed
actions for all 120 records with per-record `policy_ref` and any guardrail blocks.

---

## Day 3 — Hands (~10h)

**Done when:** a full batch runs end-to-end against Razorpay test mode and the
scoreboard prints real recovered rupees.

- [x] **3.1 Executor** (2.5h)
  - Payment Links (`POST /payment_links`), with `prefill_method` from policy
  - Retry / fresh order; subscription retry
  - Idempotency key on every write; record into `executed_actions`
  - Channel abstraction `send(channel, recipient, message)` — guardrails sit ABOVE it
- [x] **3.2 Message generation** (1h)
  - Per-cause, per-tone copy. LLM may write the text; it may NOT choose the action
  - Include the payment link; keep it short and human
- [x] **3.3 Webhook receiver** (2h)
  - FastAPI endpoint, HMAC-SHA256 signature verification (webhook secret)
  - Handle `payment.captured`, `payment_link.paid`, `order.paid`,
    `subscription.charged`, `payment.failed`
  - Idempotent handling — webhooks retry and can arrive twice
  - ~~Local tunnel (ngrok/cloudflared)~~ — solved by deploying instead. The
    Render service at `reclaimai-api.onrender.com` is the public URL Razorpay
    reaches; no tunnel was ever needed. `tests/test_webhooks.py` (19 tests)
    remains the evidence for the signing path, including the
    raw-bytes-vs-reserialized-JSON case.
  - **Real deliveries confirmed, then lost.** Five genuine Razorpay webhooks
    arrived on the deployed instance and were verified — `payment.captured`
    PROCESSED, `order.paid` and `payment_link.paid` ALREADY_ATTRIBUTED. Render's
    free tier has no persistent disk, the instance restarted, and `/tmp` went
    with it. `GET /api/webhooks` now returns 30 events, all `simulated: true`
    from the boot seed, and zero with `simulated: false`.
    **To have this standing at demo time**, pick one:
    1. Capture the artifact into the repo — pay a link, save the
       `simulated: false` JSON and a screenshot under `evidence/`. Proof then
       lives in git rather than on an ephemeral disk. Preferred: re-doing it
       live minutes before a demo is the option most likely to fail.
    2. Re-pay a link shortly before presenting.
    3. Add the persistent disk — the `disk:` block is already in
       [render.yaml](render.yaml), commented out. Paid plan.
- [x] **3.4 Outcome attribution** (1.5h)
  - link paid → find intervention → mark record RECOVERED → attribute ₹
  - This chain is the proof that the recovery was ours
- [x] **3.5 Batch runner / orchestrator** (2h)
  - `detect → diagnose → decide → guardrail → execute → persist → audit`
  - Handles deferred actions on the next tick
  - `/tick` endpoint + CLI so the demo can advance time on command
  - Time-travel flag (simulate "it is now the 1st") so salary-window retries are
    demoable in 5 minutes
- [x] **3.6 Scoreboard computation** (1h)
  - ₹ at risk / recovered / open / unrecoverable, rate by root cause,
    guardrails fired by type, escalations, contacts-per-recovery

**Day 3 checkpoint:** `python -m reclaim.cli run-batch` completes 120 records and
`python -m reclaim.cli scoreboard` prints the PROJECT.md §9 scoreboard. A single
tick only fires the actions that are *due*; `cli demo` walks the whole schedule.

---

## Day 4 — Face + Proof (~10h)

**Done when:** the demo has been rehearsed end-to-end twice without a crash.

- [x] **4.1 API layer** (1h)
  - `GET /api/scoreboard`, `/api/records`, `/api/records/{id}/audit`,
    `/api/human-queue`, `POST /api/run-batch`, `POST /api/tick`
- [x] **4.2 Dashboard screen** (3h)
  - Big numbers: at risk / recovered / rate
  - Recovery-by-root-cause bar chart
  - Guardrails-fired breakdown
  - Baseline-vs-ours comparison
- [x] **4.3 Recovery queue screen** (1.5h)
  - Table: record, amount, root cause, state, next action, next action time
  - Badge for deferred/blocked with the reason
- [x] **4.4 Audit trail screen** (1.5h)
  - Click a record → full timeline: detected → diagnosed (with reasoning +
    evidence_used) → policy_ref → guardrail verdict → executed → outcome
  - **This screen is demo beat #3. Make it readable, not pretty.**
  - Built as one page with client-side tabs, not three routes: a static export
    with no navigation cannot 404 on stage.
- [x] **4.5 Baseline runner** (1h)
  - Naive strategy over the same batch: retry everything 3x immediately, message
    every failure, ignore quiet hours
  - Produces the comparison numbers
- [x] **4.6 Full run + metrics capture** (1h)
  - Reset, generate, run, screenshot the scoreboard
  - Save the numbers somewhere quotable
- [x] **4.7 Demo rehearsal** (1h)
  - Follow PROJECT.md §13 exactly, twice, with a timer — see `DEMO.md`
  - Pre-staged graceful failure: `run-batch --kill-razorpay` (no live code edit)
  - Guardrail test output ready in a second terminal

---

## Risk register

| Risk | Mitigation | Outcome |
|---|---|---|
| Webhooks do not reach localhost | ~~Set up the tunnel on Day 1~~ — deploying to Render gave Razorpay a public URL instead | **Solved.** Five real deliveries verified, then lost to Render's ephemeral disk |
| Razorpay test mode behaves oddly | `DRY_RUN` mode + recorded fixtures so the demo never depends on live API | Held |
| Day 3 slips into Day 4 | Cut UI polish first (§4.3 queue screen), never cut §4.5 baseline or §4.7 rehearsal | Not needed |
| LLM latency stalls the batch | Signature cache + semaphore + deterministic fallback | Held. The real limit was free-tier *quota*, not latency — see 2.3 |
| Demo crashes on stage | Rehearse twice; keep a recorded fallback video of a successful run | Rehearsed; see `DEMO.md` |
| **Unforeseen:** a fixture that cannot be diagnosed | — | Layer 2 scored 0% until the generator was fixed. Caught only by running it live |
| **Unforeseen:** deployed numbers disagreeing with published ones | — | Boot seed ran without a diagnoser, and without the full arc. Both fixed; the deployment is now checked against the README, not assumed |

## After Day 4 — what continued

The four days closed with everything above ticked. What followed is recorded here
so the plan does not read as if the project stopped when the checkboxes ran out.

- [x] **Deployment** — Render (API) + Vercel (dashboard). See [DEPLOY.md](DEPLOY.md).
      This is what replaced the tunnel and made real webhook delivery possible.
- [x] **Error codes verified against Razorpay's published list** — caught 16 map
      keys and 3 generator strings Razorpay never emits. See 1.2.
- [x] **Layer 2 live on a free tier**, and the fixture fix that let it work:
      70.0% → 97.5% overall diagnosis accuracy. See 2.3.
- [x] **Deployed scoreboard reconciled with the README** — the boot seed now
      walks the full arc *and* passes its diagnoser, so the published number and
      the live one are the same number.
- [ ] **Re-capture live webhook proof into the repo** — the five verified
      deliveries were lost with Render's `/tmp`. Pay one link, save the
      `simulated: false` JSON under `evidence/`. Proof on an ephemeral disk has
      an expiry date; proof in git does not. *(Still open after Day 5.)*
- [ ] **Rotate the Gemini API key** after the buildathon — it was handled in
      plaintext during setup. *(Still open after Day 5.)*



---

## Day 5 — V2 (one day)

`PROJECT.md` §12 had already decided the scope: (a) dynamic rules, (b) B2B
receivables + promise-to-pay, (c) voice stays a slide. This was not a scoping
day, it was an execution day.

**Done when:** 180 records run end to end, the V1 120 still reproduce byte for
byte, and `pytest` is green.

### B — receivables and promises

- [x] **B1 Taxonomy** — five new `RootCause` members, `RecordState.PROMISED`,
      `ReplyIntent`, `PromiseState`, `Stage.REPLY`. All four files moved together
      per CLAUDE.md; `cli verify` caught the fifth thing nobody listed, which was
      that the coverage check itself had become wrong.
      Added `CAUSES_FOR_LEAK`: coverage is now per leak type in both directions,
      and the tool schema offered to the model is **narrowed** to the causes a
      leak type can actually have. The closed-set guarantee got tighter, not
      looser — the receivables model is never offered `EXPIRED_INSTRUMENT`.
- [x] **B2 Invoice data** — 60 overdue invoices, drawn from `Random(seed + 1)`
      **appended after** the payments stream. This was risk #1 and it was real:
      sharing the stream would have shifted all 120 existing records and
      invalidated every published number, invisibly, while the batch still ran
      and still looked right. Verified by digest, not by eye —
      `d905a053e94ccd8f6a1aac4ad5ec4eb520a6289df98c0e5e876b8ab067730514`,
      unchanged, still ₹8,24,984.
      `--leak-types` filters AFTER every draw, so it returns a subset rather than
      a different batch.
- [x] **B3 Dunning ladder** — `ladder:` in `policies.yaml`, one new field. Day 1
      polite → day 7 firmer + link → day 15 CC finance manager → day 30 human.
- [x] **B4 Promise state machine + guardrail 14** — the promise is a guardrail,
      not a special case in the runner. See PROJECT.md §12b for why that choice
      is the whole design.
- [x] **B5 Reply intent extraction** — `CachedDiagnoser` generalised over its
      result type rather than copied, so caching, the concurrency cap, the
      free-tier pacing and never-raise are in one place for both jobs.
      Deterministic date validation sits between the model and the state machine.
- [x] **B6 Simulated replies** — including Hinglish, because that is how these
      actually arrive and it is what makes the voice slide honest.
- [x] **B7 DSO + promise counts on the scoreboard**, value-weighted.

### A — rules studio

- [x] **A1 DB-backed rules behind the one loader** — no call site changed.
- [x] **A2 Admin API + validation** — every shipped default passes its own
      validator, and every dangerous edit is refused with a readable reason.
- [x] **A3 What-if replay** — two arcs against a scratch database, frozen
      diagnoses, and a `verify.py` check that the live database and the demo
      clock are untouched.
- [x] **A4 Rules studio UI** + promises/replies screen + DSO on the dashboard.

### Cross-cutting

- [x] `tests/test_promises.py`, `test_conversation.py`, `test_receivables.py`,
      `test_rules_admin.py` — and a **third property invariant**: no contact ever
      lands inside a promise window.
- [x] Four new `verify.py` checks, and two V1 checks sharpened — guardrails are
      now counted against the registry rather than a literal, so a rule that
      exists as a file but was never registered fails instead of passing.

### Two things found on the way, both worth keeping

**The value ceiling had to become per leak type.** ₹50,000 is a sensible bound on
autonomous authority for a failed consumer card, and seven records in a hundred
exceed it. Pointed at B2B receivables, where a routine invoice is ₹2 lakh, the
same number sent 48 of 60 invoices to a human — not restraint, a queue nobody can
work. The ceiling is a judgement about a KIND of money, so it is now configurable
per leak type, with a missing entry inheriting the STRICTEST value: a config gap
must never widen authority. This is also the best argument for the rules studio
existing, since it is exactly what a merchant discovers on their second day.

**The test suite was wall-clock dependent and nobody knew.** Several runner tests
passed before 20:00 IST and failed after it, because quiet hours were doing
exactly their job at the one moment nobody was watching for it. Batches in tests
now run from a pinned daytime `frm`. Related, and fixed with it: `executed_at`
was stamped from the clock rather than from the batch's own time, so a batch
running at 11:00 wrote rows dated 20:14 — and the schedule anchor for attempt
N+1 and the seven-day frequency window are both read back off that column.

Both Day 4 carry-overs — the live webhook artifact and the key rotation — are
still open and are still tracked above rather than restated here.

---

---

## Day 6 — proving the model earns its place

Prompted by reading five rival submissions. One of them (`recoup`) ran a real
ablation on its own LLM, found the model made outcomes **worse by 21 points**,
and published it with a confidence interval. That is a higher standard than we
were meeting: we had been asserting layer 2 was worth having on the strength of
a *diagnosis accuracy* number, which is a proxy for the thing that matters.

**Done when:** the same batch runs with and without layer 2, the deltas carry
intervals, and the harness refuses to print a comparison it cannot stand behind.

- [x] **6.1 Close resolved human-queue rows** — `HumanQueueRow.resolved_at` had
      existed since Day 1 and **nothing ever wrote to it**, so a record
      escalated on Monday and paid on Friday stayed on somebody's list for ever.
      Guardrail 11 stops the AGENT chasing money that already arrived; nothing
      stopped a PERSON being sent to do it.
      Fixed at both terminal points — attribution when a record reaches
      RECOVERED, and `_close` when a stopping rule fires. The scoreboard now
      reports `escalations`, `escalations_open` and `escalations_resolved`
      separately, because "54 raised" and "43 still need a person" are both true
      and only one of them is the number to quote at a merchant staffing this.
      *This had to land first:* the ablation reports Δ human escalations, and
      the bug inflated the arm with more recoveries — which is the arm with
      layer 2 on. Measuring on top of it would have understated our own model.
- [x] **6.2 Tier + expected-value ordering for the queue** — it sorted by amount
      descending, so a revoked mandate worth ₹80,000, where the correct action
      is none, outranked a ₹40,000 payment waiting on a signature.
      Three hard tiers (blocking / judgement / for-the-record), then within a
      tier `amount × P(recover|cause) × attempt decay × time decay`. Priors are
      imported from `synthetic/outcomes.py` rather than copied, so the two
      numbers cannot drift. `UNKNOWN` has no cause and therefore no prior — it
      is scored on the mean of what it could turn out to be and the row says
      **estimate** rather than printing a confident number we do not have.
      The score orders a list; it never chooses an action.
- [x] **6.3 The ablation** — `reclaim/experiments/ablation.py`, `cli ablation`.
      Two arms, two scratch databases, same seed, real runner and real
      guardrails throughout. Unlike the what-if replay, diagnoses are **not**
      frozen: there, rules never affect DIAGNOSE; here diagnosis *is* the
      independent variable. Bootstrap 95% CIs over **paired** resamples, stdlib
      only.
- [x] **6.4 Two void conditions** — above 25% unanswered layer-2 calls, no
      comparison is printed at all. Not a warning above the table — *no table*,
      because a reader who skims takes the table. The quieter condition is
      **zero calls**: with no key configured both arms are the same arm and
      every delta is zero by construction, which would read as "the model makes
      no difference". Both refused, both tested.
- [x] **6.5 `docs/RESULTS.md`** — the finding, and what it cannot show.

- [x] **6.6 The evidence table** — `README.md` now opens with claim → code →
      test name → measured number, and `tests/test_readme_claims.py` asserts
      every cited test and every linked file actually exists. Prose does not
      fail to compile, so a rename would otherwise turn a row into a claim with
      nothing behind it. Verified by deliberately renaming a citation and
      watching the guard fail.
- [x] **6.7 Real vs modelled table** — what is live (Razorpay APIs, error codes,
      HMAC over raw bytes, the attribution walk, every model call, all 14
      guardrails) against what is modelled (whether a customer pays), plus three
      limitations stated rather than waited for: the priors are estimates, there
      is no self-cure path, and layer 2 misreads one `RISK_DECLINE`.
- [x] **6.8 Fixed a stale figure in the honesty section** — the README itemised
      the baseline gap as `₹2,41,069` from an older run when it is `₹23,52,777`.
      A stale number is bad anywhere and worst in the section arguing we do not
      inflate. Now current, and it makes the better point: of the whole gap,
      **₹1,710 across 3 records is the only part where our strategy was simply
      worse.** Everything else is a rule doing its job.

### The result, and the honest caveat

On the 69 records layer 2 answers: **+₹12,74,886 recovered, 51 fewer human
escalations, 0 harmful actions**, every interval excluding zero, for **38 API
calls** — the signature cache turns 412 consultations into 38 requests. With the
model off, all 69 become `UNKNOWN` → `no_auto_action` → a person, which is not a
queue, it is a backlog nobody works.

**The money delta is an upper bound and the file says so.** The outcome simulator
only recovers a record when an intervention fires, so the "off" arm scores ₹0 by
construction rather than by measurement. Real customers sometimes pay unprompted;
`recoup` has a self-cure baseline (organic 46 vs 45 across arms) and we do not.
The escalation delta does not have this problem — those rows are counted.

**The run is reproducible only up to the model's own variance**, which is also
recorded rather than smoothed over. The seed fixes the batch, the rules and the
arc; it does not fix the model. Two runs gave identical rupees, records and
contacts, and escalations of 19 then 18 — one borderline record labelled
differently. The deterministic arm is exactly reproducible; the arm with a model
in it is not, and saying otherwise would be the kind of claim this whole exercise
exists to stop us making.

The one thing worth stating plainly: we built the measurement before running it
and would have published a null result. That is the whole point of having built
it.

## Non-negotiables (if everything else burns down)

1. Guardrail test suite green
2. Idempotency provable
3. Scoreboard with a baseline comparison
4. Audit trail readable for one record
5. One blocked action shown on stage
