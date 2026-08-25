# ReclaimAI — AI Revenue Recovery Agent

Razorpay Buildathon, Track 03. Detects revenue at risk, diagnoses *why* it failed,
decides a bounded intervention, and refuses to act when it shouldn't.

**The core design rule: the LLM never touches money.** It produces a label from a
closed enum. A deterministic policy table turns that label into a proposed action.
A deterministic guardrail engine decides whether the action may fire.

---

## Verify in 60 seconds

No credentials required. `DRY_RUN` is the default, so a fresh clone runs the whole
pipeline without a Razorpay or Anthropic key.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Linux/mac: .venv/bin/python

.venv/Scripts/python -m reclaim.cli verify     # structural self-audit
.venv/Scripts/python -m reclaim.cli seed       # generate the batch (seeded)
.venv/Scripts/python -m reclaim.cli detect     # -> 120 records, ₹6,92,056 at risk
.venv/Scripts/python -m reclaim.cli diagnose   # accuracy vs ground truth
.venv/Scripts/python -m reclaim.cli plan       # proposed actions + guardrail blocks
.venv/Scripts/python -m pytest -q              # 123 passed
```

Every command takes `--json`.

**The batch is seeded.** `seed 42` produces the same 120 records and the same
`₹6,92,056` total on every machine, every run. Numbers quoted here are meant to be
reproduced, not trusted.

---

## `cli verify` — the build audits itself

CLAUDE.md requires that adding a `RootCause` updates four places at once. Rather
than trusting that, [reclaim/verify.py](reclaim/verify.py) checks it mechanically —
along with every other structural claim this README makes. Checks for components
not yet built report `TODO`, not `PASS`, so the output stays honest about what day
of the build it is.

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
TODO  deterministic map matches harvested codes  (needs `cli harvest`)
```

---

## Two guarantees enforced by the database, not by discipline

**No double-charging.** `executed_actions.idempotency_key` is `UNIQUE`, and the key
is *derived* from `(record_id, attempt_number, action_type)` as a property — never
passed in, so it cannot drift from the tuple it represents.
[db.py](reclaim/db.py) · [models.py](reclaim/models.py)

**No edited audit trail.** `audit_log` carries SQLite triggers that `ABORT` on
`UPDATE` and `DELETE`. Append-only is a property of the database, not a convention
someone can forget. [db.py](reclaim/db.py)

Both are proved in [tests/test_foundation.py](tests/test_foundation.py), not asserted here.

---

## Diagnosis is measured, not asserted

The generator records the cause it planted in every record, so accuracy is
scored against ground truth rather than demonstrated by anecdote:

```
DIAGNOSIS ACCURACY  (n=120, ground truth known by construction)
  deterministic      69 records   100.0% correct
  cohort             15 records   100.0% correct
  fallback           36 records     0.0% correct   <- layer 2's job
```

**The cohort signal earns its place.** Fifteen records on one issuer inside one
hour carry a generic "declined by the bank" error. Read alone, each is a
customer-side failure worth a message. Read together, the issuer is down at a
0.71 failure rate against a 0.045 baseline — a 15.75x ratio — so the agent stays
silent and retries in twenty minutes. **Fifteen needless customer contacts
prevented**, and the counterfactual is computed, not claimed.

## Restraint, in numbers

```
The agent wanted to take 120 actions.
It was allowed 54.
66 were blocked — 17 deferred, 43 sent to a human.

   confidence_floor       40
   cooldown               17
   value_ceiling           7
   consent                 5
   dnd                     3
```

Every refusal carries its reason and what happens next: a time to retry, a human
to route to, or a permanent stop. `audit_log` records blocks as loudly as
executions.

## The LLM never touches money

Layer 2 runs only on the ~40% of records an error string cannot resolve, using
forced tool use against a closed enum. The model cannot invent a root cause —
only pick a wrong one from a fixed list, which the policy table and the thirteen
guardrails below it still contain. A schema violation becomes `UNKNOWN` and
reaches a human; it never becomes a guess.

`--no-llm` is a real code path, not a mock: the batch completes with the API
down. [tests/test_llm_diagnosis.py](tests/test_llm_diagnosis.py) proves it
against a fake client, so none of it needs a key to verify.

---

## Layout

```
reclaim/
  enums.py          closed enums — RootCause is why hallucination is harmless
  models.py         Pydantic boundary models; AtRiskRecord stays V2-generic
  db.py             six tables + the two database-level guarantees
  timeutil.py       IST, quiet hours, next_salary_window
  money.py          paise -> ₹5,84,300 / ₹5.84L
  verify.py         structural self-audit
  detectors/        one plugin per leak type -> detect() -> list[AtRiskRecord]
  synthetic/        seeded leak generator + outcome simulator
  executor/         Razorpay wrapper: idempotency, backoff, DRY_RUN
  audit/            append-only decision log
  brain/            diagnosis / policy / guardrails      (Day 2)
  webhooks/ api/    outcome attribution, FastAPI routes  (Day 3-4)
```

## Honesty note

Real Razorpay APIs, real error-code shapes, real payment links. **Customer
response is modelled** by [synthetic/outcomes.py](reclaim/synthetic/outcomes.py) —
per-cause success probabilities, stated openly rather than presented as live
conversion data.
