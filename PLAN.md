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
- [x] Day 6 — proving the model earns its place
- [x] Day 6b — the self-cure baseline
- [x] Day 7 — the previewable agent (phases 1–4; phase 5 deferred)

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

On the 69 records layer 2 answers, as first measured: **+₹12,74,886 recovered,
51 fewer human escalations, 0 harmful actions**, for **38 API calls** — the
signature cache turns 412 consultations into 38 requests.

**The money figure was an upper bound, and Day 6b closed the gap rather than
leaving it disclosed.** The simulator only recovered a record when an
intervention fired, so the "off" arm scored ₹0 by construction. See Day 6b for
what the number became once customers could pay unprompted: **+₹5,92,424**, less
than half. The escalation delta did not move, because those rows are counted.

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


---

## Day 6b — the self-cure baseline

The limitation Day 6 published turned out to be worth closing rather than
disclosing. The simulator only recovered a record when an intervention fired, so
it asserted that nobody ever pays unless chased — which flatters any agent that
chases, and made the ablation's money figure an upper bound by about 2×.

- [x] **6b.1 Self-cure drawn per record** — `SELF_CURE` per cause in
      `outcomes.py`, dated within 30 days of detection, from `Random(seed + 2)`
      after every other draw and stored on the batch rather than on a record.
      The reproducibility digest and ₹1,12,09,814 are untouched, checked by
      computing the digest with and without the change rather than assuming.
- [x] **6b.2 Organic recoveries delivered through the real webhook path** —
      signed, verified, and landing as `ORGANIC`: the money arrived and none of
      it is the agent's. Its own scoreboard bucket, so it neither inflates
      recovery nor gets written off as money that never came back.
- [x] **6b.3 The bug this exposed, which is the important part.** Our executor
      writes `notes.record_id` on everything it mints, so that note is proof the
      money came through us. The first version forged it on organic payments —
      and attribution duly adopted them, crediting the agent with 23 recoveries
      it had not caused. An unprompted payment now arrives against the
      MERCHANT's original order reference and carries no note of ours, so
      nothing can claim it. ₹4L moved from "ours" to "theirs" on that one fix.
- [x] **6b.4 A second ordering bug, found by 6.1's verify check.** Settlement
      runs before replies are read, so a reply can arrive after its record was
      already paid — and both queue writers added the row before checking
      whether the record was terminal, putting a settled record back on
      somebody's desk. Guarded in `runner._queue_for_human` and
      `conversation.handler._to_human`.
- [x] **6b.5 Incremental figures for both strategies** — a record recovered on
      day one from a customer who would have paid on day twenty is money that
      was always going to arrive. Netting it out is what the comparison is
      actually asking.
- [x] **6b.6 `verify` check for arm parity** — the draw is identical in every
      arm and no cause rated zero ever self-cures. The property recoup found the
      hard way when its control arm stopped being observed sooner and was
      credited fewer unprompted payments for a bookkeeping reason.

**What it cost us.** The ablation's headline fell from +₹12,74,886 to
**+₹5,92,424** — layer 2 cannibalises ₹1,51,595 of money that was arriving
anyway. The naive comparison moved the other way: net of the 21 self-cures the
naive run banked and claimed against our 10, it is **₹27,50,189 against
₹19,02,912** rather than ₹47.6L against ₹21.4L, so roughly two thirds of its
apparent lead is money that was coming regardless.

The self-cure rates are now the least defensible numbers in the project and the
README says so: the ordering is arguable, the magnitudes are estimates, and
every figure above moves with them.

## Day 7 — the previewable agent

The system does about two dozen things and the dashboard shows six of them, every
one as the settled result of a batch that already ran. A judge can read what the
agent did. They cannot hand it anything. And the work that most distinguishes
this build — the ablation, the idempotency proof, the attribution walk, `verify`
— is a `cli` subcommand nobody watching a demo will ever type.

Day 7 closes both gaps with one idea: **a visitor's input becomes a real record.**
Not a simulation of one. They type an unseen error string, watch it get diagnosed,
actioned and gated stage by stage, press commit, and it lands on the dashboard, in
the human queue and in the audit trail like any other record — because it goes
through the same runner, the same gate and the same executor.

That is also the risk. The published figures rest on a batch that reproduces byte
for byte, and B2 already taught this lesson once: sharing a random stream shifted
every downstream record invisibly while the batch still ran and still looked right.
So user records are a separate ID space appended after every seeded draw, and the
digest is checked with and without them rather than assumed.

**The day is ordered by preview earned per hour, and it stops cleanly after any
phase.** Phase 1 is the demo. Phase 2 buys three more features at the cost of one,
because they reuse a component phase 1 already paid for. Phase 4 is the only part
that is not a preview, and it is also the only part that must not be cut — see the
note under it.

**Done when (phase 1):** an unseen error string typed into the dashboard becomes a
committed record visible on the dashboard, the queue and the audit trail, and the
180-record digest is unchanged.

### Phase 1 — the core loop (~8.5h)

Stop here and the demo works: type, watch, commit, see it land.

*State: complete. 396 tests and 28 verify checks green. See
[docs/DAY7-HANDOFF.md](docs/DAY7-HANDOFF.md) for the traps, the bugs found on the
way, and the limitations worth knowing before demoing.*

- [x] **7.1 A separate ID space for what a visitor makes** *(1.5h, took ~1h)*.
      `USR_` alongside `REC_` and `INV_`, minted from the highest id already
      stored rather than a row count — `audit_log` is append-only, and a reused
      id would graft a stranger's submission onto an older record's history.
      Provenance also on `raw_signals["origin"]` for readability, but the prefix
      is what the queries filter on: no migration, and `LIKE 'USR_%'` reads the
      same in SQL, in a log line and to a person scanning the audit trail.
      **Three of the four exposures this item was written to close do not
      exist,** which is worth recording rather than quietly not doing. The
      reproducibility digest compares `generate(seed=42)` against itself and
      `generate` is a pure function of its seed — nothing in a database can
      reach it. The ablation seeds scratch databases the same way. The baseline
      reads live rows but only ever looks them up by an id drawn from the seeded
      batch, so an unknown id contributes zero.
      **The scoreboard was the real one,** and it was worse than the plan
      assumed: not only the money, but `contacts`, `interventions`,
      `escalations`, `webhooks_attributed`, `replies_read` and the guardrail
      counts, because `contacts_per_recovery` is a published figure (2.45) and a
      visitor's contacts would have drifted it while the headline held still.
      Every aggregate in `compute()` is now restricted to the seeded population
      and visitor records are counted in their own bucket, on the same reasoning
      that keeps organic money out of `recovered_paise`.
- [x] **7.2 Preview and commit are one code path, not two** *(1h, took ~1.5h)*.
      `reclaim/sandbox.py`. Preview calls `diagnose_batch`, `decide` and
      `gate.run` — the same three functions the runner calls, in the same order.
      Commit hands the record to `run_batch(only={id})` and lets the runner do
      all of it, so a submission inherits the gate, the idempotency key and the
      audit rows rather than a private copy of any of them. `only=` is four lines
      in the runner and is what stops a demo button re-proposing 180 records.
      The commit trace is read back off the AUDIT LOG rather than reported from
      memory, on the same principle as the scoreboard: a claim about what
      happened that cannot be recovered from what was written down is a claim
      nobody should believe, including us.
      Measured, not asserted: five previews wrote 0 records, 0 audit rows and 0
      interventions; one commit wrote 1, 4 and 1. Preview runs
      the real diagnosers, the real policy table and the real gate over an
      in-memory `AtRiskRecord` and returns a trace; commit runs the identical call
      and then persists through the runner. A second implementation would be a
      second thing that can disagree with the runner, which is the bug class this
      project exists to avoid.
      **Preview must not borrow the what-if's scratch database.** `use_database`
      rebinds a module global, is process-wide, is documented as not thread-safe,
      and is safe today only because the busy flag serialises one long batch at a
      time. A preview is short, interactive and concurrent — the opposite shape —
      and two of them interleaving would rebind the database under a request
      mid-write. It does not need one: diagnosis, policy and the gate are pure,
      guardrail context is a READ of the live database, and reads are free.
      Preview writes nothing, so there is nothing to isolate. Only commit writes.
- [x] **7.3 A committed record gets an outcome** *(0.5h)*, drawn from the same
      `outcomes.py` priors through the real `settle()`, so it resolves rather than
      sitting in the queue for ever — the bug 6.1 already fixed once.
      **No self-cure draw.** Self-cure exists to keep the ablation honest, and 7.1
      excludes user records from the ablation population, so drawing one here
      would be machinery serving nothing.
      **A submission has no hidden ground truth,** and pretending otherwise would
      have been the subtle error here. The generator plants a truth the diagnosers
      can be wrong about; a visitor typing "card expired" is not concealing a
      different answer — their description IS the fact of the matter. So the
      outcome is drawn against the diagnosed cause, and the consequence is stated
      rather than left to be found: a committed record can never be a diagnosis
      error and must never be counted in accuracy. It is not — `/api/diagnosis`
      scores a freshly generated batch and cannot see the database at all.
      `settle()` gained the same `only=` the runner did, because settling
      unfiltered would have settled the seeded batch as a side effect of somebody
      pressing a demo button, moving the published figures — the one thing the
      `USR_` split exists to prevent.
- [x] **7.4 Reset is one button, on the same screen** *(0.5h)*.
      `POST /api/sandbox/reset` — restores the committed snapshot and resets the
      demo clock, behind the same busy flag every other write uses. Restores the
      committed snapshot, which already exists. A demo that lets strangers add
      records needs the way back to be obvious, not a CLI command.
- [x] **7.5 The trace strip** *(2.5h)*. `ui/src/components/Trace.tsx`. Stage cards filling left to right, each
      badged by who decided it — model, table, gate. CLAUDE.md's one rule becomes
      something a judge sees rather than something we claim: the model badge
      appears on exactly one card. Raw tool-call JSON expandable per stage; the
      full prompt drawer is deferred to phase 5.
- [x] **7.6 `/api/sandbox/preview`, `/commit`, `/reset`, `/presets`** *(1.5h)*, returning a uniform
      `trace: [{stage, decided_by, output, why}]` so the strip stays generic across
      every mode added later. Model calls go through `CachedDiagnoser`, so a
      repeated input costs nothing and the presets ship pre-warmed — the demo path
      never waits on free-tier quota.
- [x] **7.7 The Try-it tab** *(1h)*, with a mode switcher hosting all three of phase 2 — preset inputs beside a free-text box, the
      trace strip, the commit button and the reset button.

### Phase 2 — three features for the price of one (~2.5h)

The strip and the trace shape are paid for. Each mode below is now an endpoint and
a config object, which is the whole reason phase 1 built a generic component.

- [x] **7.8 Reply / promise mode** *(1h)*. Type Hinglish, get `PROMISE_TO_PAY`
      plus a validated date, and the effect: the agent goes silent. An **advance 7
      days** button over the existing `/api/tick` shows the promise kept or broken.
      This replaces the full time scrubber; the promises tab already renders the
      book, so the slider was buying a second view of a screen we have.
- [x] **7.9 Guardrail simulator mode** *(1h)*. Eight scenarios, each firing the rule it names; `promise_window` needed `ctx.extra["promised_for"]` as a datetime, and silently passed until that was found. A hypothetical action against all
      fourteen rules, greens collapsed, blocks expanded with the threshold that
      tripped and the `guardrails.yaml` line it came from. The blocks are the demo.
- [x] **7.10 Kill-the-model toggle** *(0.5h)*, on both the classify and reply modes. Degrades the chain live — L1 miss,
      L2 unavailable, UNKNOWN, human queue. Rule 7 demonstrated instead of
      asserted, and it is a flag on an endpoint that already exists.

### Phase 3 — the evidence tab (~2.5h)

- [x] **7.11 Evidence artifacts** *(1h)* — `reclaim/evidence.py`, `reclaim evidence [--only ...]`. All three committed; the ablation measured +₹6,07,926, 38 fewer escalations, 0 harmful actions, 38 API calls, 0% unanswered, produced by the `--json` flags that
      already exist: `evidence/ablation.json`, `evidence/baseline.json`,
      `evidence/verify.json`. Committed, stamped with seed and date, served by
      `/api/evidence/{name}`. This also closes the Day 4 carry-over about proof on
      an ephemeral disk having an expiry date.
- [x] **7.12 The Evidence tab** *(1.5h)* — claim in English, the measured number
      with its interval, the test name that guards it. The ablation card carries
      its void conditions and the line about having built the measurement before
      running it. Rendered from committed JSON; the **Run it live** hatch, the
      attribution walk and 6b.3's forged-note toggle are deferred to phase 5.

### Phase 4 — the part that must not be cut (~1.5h)

- [x] **7.13 Two `verify` checks** *(0.75h)*. The second landed early, with 7.1,
      because a filter nobody checks is a filter that rots:
      `_user_records_do_not_move_the_published_figures` commits a ₹99,000 visitor
      record in a scratch database and asserts every non-`user_` figure is
      byte-identical either side of it. Still to write: a preview leaves no audit
      row, no intervention and no clock movement. **Done** —
      `_sandbox_preview_leaves_no_trace` runs all five presets against the LIVE
      database and counts rows and the clock either side, which it can do
      precisely because a preview writes nothing. Both checks pass.
      *(It also caught the `use_database` trap first-hand — binding
      `SessionLocal` by name on the way into the block gets the LIVE database,
      because the rebind replaces a module global. The check now reads it through
      the module inside the block, and says so in a comment, since the same trap
      is waiting for 7.6.)*
- [x] **7.14 `tests/test_sandbox.py`** *(0.75h)* — 17 tests, green. Preview
      writes nothing and moves no clock; a layer-1 hit never carries the model
      badge; an unmapped error falls through and says so; `without_model` still
      completes and reaches a human; commit is idempotent under a repeated
      submit; a committed record obeys the frequency cap like any other; and no
      committed record moves a published figure.
      **It caught a real bug on the first run.** `next_user_id` read the
      high-water mark only from the records table, so deleting a committed record
      handed the next submission the same id — grafting a stranger's words onto
      an older record's audit history, which is precisely what the function's
      own docstring said it prevented. It now reads the append-only audit log
      too.

**Why this phase survives a time cut and the previews do not.** Everything above it
is a way of showing what the system does. This is what stops a visitor's input
quietly moving `₹27,44,651`, the digest, or a published figure the README cites and
`test_readme_claims.py` guards. A demo that impresses beside a number that no longer
reproduces is a worse outcome than a smaller demo. If the clock runs out, drop
phase 3, then phase 2 — never 7.1 or 7.13.

### Phase 5 — deferred, and deliberately

The full prompt drawer, the live-run hatch on the ablation, the attribution walk
rendered arrow by arrow, the forged-note toggle from 6b.3, the day-by-day scrubber
with its diff panel, and the tier 3–5 modes: detector sandbox, policy lookup, rules
validation refusal, queue re-sort comparison, cohort lens, message composer, dunning
ladder. Each is an endpoint plus a config object against a component that will
already exist — which is the point of building it this way, and the reason none of
them are urgent.

## Non-negotiables (if everything else burns down)

1. Guardrail test suite green
2. Idempotency provable
3. Scoreboard with a baseline comparison
4. Audit trail readable for one record
5. One blocked action shown on stage
