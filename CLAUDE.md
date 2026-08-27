# CLAUDE.md — working conventions for this repo

**Project:** ReclaimAI — AI Revenue Recovery Agent for the Razorpay Buildathon (Track 03).
**Read `PROJECT.md` for the spec and `PLAN.md` for the schedule before doing anything.**
`PLAN.md` checkboxes are the source of truth for progress — find the first unchecked
item and continue from there. Tick them off as work completes.

Solo developer, 4-day build. Optimise for *working and provable*, not elegant.

---

## The one rule that governs the whole design

**The LLM never touches money.**

It produces a *label* from a closed enum (`RootCause`) plus a confidence score.
A deterministic policy table turns that label into a `ProposedAction`.
A deterministic guardrail engine decides whether that action may fire.

Never let model output directly trigger a Razorpay write call. If you find yourself
writing "ask the model what to do", stop — the model diagnoses, the table decides,
the gate permits.

The model MAY write customer-facing message *text*. It may NOT choose the action,
the amount, the timing, or the recipient.

---

## Stack

- Python 3.11+, FastAPI, SQLAlchemy, SQLite (file-based, zero setup)
- `razorpay` official SDK — **test mode only** (`rzp_test_...`)
- `anthropic` SDK (`claude-sonnet-5`) or `google-genai` (`gemini-3.5-flash-lite`,
  free tier) — forced tool use either way. Both diagnosers share one prompt and
  one schema; add a provider by subclassing `CachedDiagnoser`, never by copying it
- Pydantic v2 at every boundary
- pytest + hypothesis
- Frontend: Next.js + Tailwind + shadcn/ui

## Layout

```
detectors/     one plugin per leak type, each exposing detect() -> list[AtRiskRecord]
brain/
  rules.py     THE single rule loader — V2 swaps its source for a DB
  diagnosis/   deterministic.py (layer 1), llm_diagnoser.py (layer 2), cohort.py
  policy/      policies.yaml + engine.py
  guardrails/  base.py + rules/ (one file per guardrail) + registry.py
executor/      Razorpay writes, channel abstraction
webhooks/      signature verification + outcome attribution
audit/         append-only decision log
api/           FastAPI routes
ui/            Next.js
tests/
```

---

## Non-negotiable coding rules

1. **Guardrails fail closed.** `evaluate_all` never raises. Malformed input → BLOCK,
   not an exception. A guardrail that throws is one that gets silently skipped later.
2. **Every Razorpay write carries an idempotency key** derived from
   `(record_id, attempt_number, action_type)`. No exceptions. This is the single most
   important property in a payments project.
3. **`audit_log` is append-only.** Never UPDATE or DELETE a row. Log blocked actions
   as loudly as executed ones — the blocks are the demo.
4. **Rules are data, not code.** Policy lives in `policies.yaml`; guardrail thresholds
   live in config. Both load through `brain/rules.py`. Do not scatter magic numbers.
5. **`AtRiskRecord` stays generic.** No payment-specific fields on it — V2 adds
   overdue invoices as just another `leak_type`.
6. **Guardrails sit above the channel abstraction**, never inside it, so a new channel
   inherits consent/DND/quiet-hours for free.
7. **Always have a fallback path.** LLM down → deterministic map → UNKNOWN → human
   queue. The batch must always complete.
8. **Money is stored in paise as `int`.** Never float. Format to ₹ only at the UI edge.
9. **All timestamps timezone-aware, IST (`Asia/Kolkata`).** Quiet hours and salary
   windows are IST concepts.
10. **Never commit `.env`.** Test keys only, but still never commit them.

---

## Testing expectations

Before moving from Day 2 to Day 3, `pytest tests/test_guardrails.py` must be green.
That suite is a deliverable, not a nicety — judges will ask "how do you know it cannot
spam someone or double-charge?" and the answer is a passing test, not a claim.

Two invariants must hold as property tests over random batches:
- no customer receives more than 2 contacts in any 7-day window
- no `(record_id, attempt_number, action_type)` executes twice

---

## Scope discipline

**In V1 (these 4 days):** three leak types, both diagnosis layers, all 13 guardrails,
execution, webhooks, dashboard, audit trail, baseline comparison.

**Deferred to V2 — do not build now:**
- Admin-editable rules (DB + admin API + admin UI)
- B2B receivables / overdue invoices + promise-to-pay
- Hinglish voice channel (roadmap slide only)

If a change would help V2 but costs V1 time, note it in `PROJECT.md` §12 and move on.

---

## Style

- Match the surrounding code. Comment density low; explain *why*, never *what*.
- Prefer boring, readable code over clever code. This gets demoed, not maintained.
- Small modules over large ones — one guardrail per file makes the demo screenshot nice.
- When adding a `RootCause`, update: the enum, `policies.yaml`, the prompt rules,
  and the outcome simulator. All four, or the batch numbers go wrong.
