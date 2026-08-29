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

## Non-negotiables (if everything else burns down)

1. Guardrail test suite green
2. Idempotency provable
3. Scoreboard with a baseline comparison
4. Audit trail readable for one record
5. One blocked action shown on stage
