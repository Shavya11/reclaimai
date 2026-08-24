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
.venv/Scripts/python -m reclaim.cli detect     # -> 120 records, ₹8,65,686 at risk
.venv/Scripts/python -m pytest -q              # 35 passed
```

Every command takes `--json`.

**The batch is seeded.** `seed 42` produces the same 120 records and the same
`₹8,65,686` total on every machine, every run. Numbers quoted here are meant to be
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
TODO  deterministic map yields valid causes      (Day 2)
TODO  policies.yaml covers every RootCause       (Day 2)
TODO  13 guardrails implemented                  (Day 2)
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
