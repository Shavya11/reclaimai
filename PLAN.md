# PLAN — 4-Day Solo Build

Read `PROJECT.md` first for the spec. This file is the schedule and the checklist.

**Budget:** ~40 hours over 4 days ≈ 10h/day, solo.
**Rule:** do not gold-plate Days 1–2. The slack you keep is what saves Day 4.

**Progress:** update the checkboxes in this file as you go. A new chat should read
`PROJECT.md` + `PLAN.md`, find the first unchecked box, and continue from there.

---

## Status

- [ ] Day 1 — Foundation
- [ ] Day 2 — Brain
- [ ] Day 3 — Hands
- [ ] Day 4 — Face + Proof

---

## Day 1 — Foundation (~9h)

**Done when:** 120 synthetic at-risk records sit in the DB, created through real
Razorpay test-mode API calls with real error codes, and `pytest` runs green on the
record/schema layer.

- [x] **1.1 Repo skeleton** (0.5h)
  - `pyproject.toml` / `requirements.txt`, `.env.example`, `.gitignore`
  - Package layout per PROJECT.md §3
  - `git init` + first commit
- [ ] **1.2 Razorpay test account** (0.5h)
  - Generate `rzp_test_` key id + secret → `.env` (NEVER commit)
  - Smoke test: create an order, fetch it back
  - Note the exact error-code strings test cards return — feed them into
    `DETERMINISTIC_MAP`
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
`120 at-risk records, ₹5,84,300 at risk`.

---

## Day 2 — Brain (~10h)

**Done when:** the full guardrail test suite is green and a record can go
error → RootCause → ProposedAction → ALLOW/BLOCK without touching Razorpay.

- [ ] **2.1 Deterministic diagnosis map** (1h)
  - `brain/diagnosis/deterministic.py` using the REAL codes seen on Day 1
  - Target: resolves ~60% of records at confidence 1.0
- [ ] **2.2 Cohort signal** (1h)
  - Group batch by (issuer, hour bucket), compute failure rate vs baseline
  - This is what turns "declined" into BANK_DOWNTIME → silent retry
- [ ] **2.3 LLM diagnosis** (2.5h)
  - Forced tool use (`record_diagnosis`), `claude-sonnet-5`
  - Pydantic validation; validation failure → UNKNOWN → human
  - Signature cache + semaphore(8)
  - Fallback chain: LLM → deterministic → UNKNOWN. **Never crash the batch.**
- [ ] **2.4 Policy engine** (1.5h)
  - `policies.yaml` per PROJECT.md §6, loader in `brain/rules.py` (ONE loader —
    V2 swaps this for DB)
  - `decide(record, diagnosis) -> ProposedAction` with `policy_ref` set
  - Schedule resolver: `20m`, `+7d`, `+1d@10:00`, `next_salary_window`, `immediate`
- [ ] **2.5 Guardrail engine** (2.5h)
  - `brain/guardrails/base.py` — Protocol, Verdict, `evaluate_all` collecting ALL
    violations, never raising
  - All 13 rules, one file each in `rules/`
  - `GuardrailResult(allowed, violations, deferred_until, requires_human)`
  - Blocked ≠ dropped: compute `deferred_until` per violation
- [ ] **2.6 Guardrail tests** (1.5h) ← DO NOT SKIP, judges will ask
  - The 8 named tests in PROJECT.md §7
  - 2 property tests (hypothesis): frequency-cap invariant, idempotency invariant
  - `pytest tests/test_guardrails.py` must be green before Day 3

**Day 2 checkpoint:** `pytest` all green; `python -m reclaim.cli plan` prints proposed
actions for all 120 records with per-record `policy_ref` and any guardrail blocks.

---

## Day 3 — Hands (~10h)

**Done when:** a full batch runs end-to-end against Razorpay test mode and the
scoreboard prints real recovered rupees.

- [ ] **3.1 Executor** (2.5h)
  - Payment Links (`POST /payment_links`), with `prefill_method` from policy
  - Retry / fresh order; subscription retry
  - Idempotency key on every write; record into `executed_actions`
  - Channel abstraction `send(channel, recipient, message)` — guardrails sit ABOVE it
- [ ] **3.2 Message generation** (1h)
  - Per-cause, per-tone copy. LLM may write the text; it may NOT choose the action
  - Include the payment link; keep it short and human
- [ ] **3.3 Webhook receiver** (2h)
  - FastAPI endpoint, HMAC-SHA256 signature verification (webhook secret)
  - Handle `payment.captured`, `payment_link.paid`, `order.paid`,
    `subscription.charged`, `payment.failed`
  - Idempotent handling — webhooks retry and can arrive twice
  - Local tunnel (ngrok/cloudflared) for Razorpay to reach you
- [ ] **3.4 Outcome attribution** (1.5h)
  - link paid → find intervention → mark record RECOVERED → attribute ₹
  - This chain is the proof that the recovery was ours
- [ ] **3.5 Batch runner / orchestrator** (2h)
  - `detect → diagnose → decide → guardrail → execute → persist → audit`
  - Handles deferred actions on the next tick
  - `/tick` endpoint + CLI so the demo can advance time on command
  - Time-travel flag (simulate "it is now the 1st") so salary-window retries are
    demoable in 5 minutes
- [ ] **3.6 Scoreboard computation** (1h)
  - ₹ at risk / recovered / open / unrecoverable, rate by root cause,
    guardrails fired by type, escalations, contacts-per-recovery

**Day 3 checkpoint:** `python -m reclaim.cli run-batch` completes 120 records and prints
the PROJECT.md §9 scoreboard.

---

## Day 4 — Face + Proof (~10h)

**Done when:** the demo has been rehearsed end-to-end twice without a crash.

- [ ] **4.1 API layer** (1h)
  - `GET /api/scoreboard`, `/api/records`, `/api/records/{id}/audit`,
    `/api/human-queue`, `POST /api/run-batch`, `POST /api/tick`
- [ ] **4.2 Dashboard screen** (3h)
  - Big numbers: at risk / recovered / rate
  - Recovery-by-root-cause bar chart
  - Guardrails-fired breakdown
  - Baseline-vs-ours comparison
- [ ] **4.3 Recovery queue screen** (1.5h)
  - Table: record, amount, root cause, state, next action, next action time
  - Badge for deferred/blocked with the reason
- [ ] **4.4 Audit trail screen** (1.5h)
  - Click a record → full timeline: detected → diagnosed (with reasoning +
    evidence_used) → policy_ref → guardrail verdict → executed → outcome
  - **This screen is demo beat #3. Make it readable, not pretty.**
- [ ] **4.5 Baseline runner** (1h)
  - Naive strategy over the same batch: retry everything 3x immediately, message
    every failure, ignore quiet hours
  - Produces the comparison numbers
- [ ] **4.6 Full run + metrics capture** (1h)
  - Reset, generate, run, screenshot the scoreboard
  - Save the numbers somewhere quotable
- [ ] **4.7 Demo rehearsal** (1h)
  - Follow PROJECT.md §13 exactly, twice, with a timer
  - Pre-stage the "graceful failure" (a kill switch for the Razorpay client)
  - Have the guardrail test output ready in a second terminal

---

## Risk register

| Risk | Mitigation |
|---|---|
| Webhooks do not reach localhost | Set up the tunnel on **Day 1**, not Day 3 |
| Razorpay test mode behaves oddly | `DRY_RUN` mode + recorded fixtures so the demo never depends on live API |
| Day 3 slips into Day 4 | Cut UI polish first (§4.3 queue screen), never cut §4.5 baseline or §4.7 rehearsal |
| LLM latency stalls the batch | Signature cache + semaphore + deterministic fallback (already in plan) |
| Demo crashes on stage | Rehearse twice; keep a recorded fallback video of a successful run |

## Non-negotiables (if everything else burns down)

1. Guardrail test suite green
2. Idempotency provable
3. Scoreboard with a baseline comparison
4. Audit trail readable for one record
5. One blocked action shown on stage
