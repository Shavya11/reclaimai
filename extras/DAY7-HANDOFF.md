# Day 7 handoff — the previewable agent

Written so a session that has never seen this work can pick it up. `PLAN.md` §Day 7
holds the checkboxes and the reasoning; this file holds the state, the traps, and
the things that are wrong or unfinished.

**Status: phases 1–4 complete. 396 tests, 28 verify checks, all green.**
Phase 5 is deferred by design and listed at the end.

---

## 1. What the day was for

The system did about two dozen things and the dashboard showed six of them, every
one the settled result of a batch that had already run. A judge could read what
the agent did; they could not hand it anything. And the work that most
distinguishes this build — the ablation, the idempotency proof, the attribution
walk, `verify` — was a `cli` subcommand nobody watching a demo would type.

Day 7 closes both gaps with one idea: **a visitor's input becomes a real record.**
They describe a failure, watch it diagnosed, actioned and gated stage by stage,
press commit, and it lands on the dashboard, in the recovery queue and in the
audit trail — through the same runner, the same gate and the same executor.

---

## 2. What was built

### New modules

| File | What it is |
|---|---|
| `reclaim/provenance.py` | The `USR_` id space and the filters that keep visitor records out of every published figure |
| `reclaim/sandbox.py` | `preview` / `commit` / `read_reply` / `simulate_guardrails`, and the presets for each |
| `reclaim/evidence.py` | Committed proof under `evidence/*.json`, with provenance stamps |
| `tests/test_sandbox.py` | 17 tests |
| `ui/src/components/Trace.tsx` | The trace strip — stage cards badged by who decided |
| `ui/src/components/TryIt.tsx` | The Try-it tab and its three modes |
| `ui/src/components/ReplyLab.tsx` | Reply / promise mode |
| `ui/src/components/Guardrails.tsx` | The guardrail simulator |
| `ui/src/components/Evidence.tsx` | The Evidence tab |

### Modified

- `reclaim/runner.py` — `run_batch(only={ids})`, four lines
- `reclaim/settlement.py` — `settle(only={ids})` and `_pending(only)`
- `reclaim/repository.py` — `save_records()` (insert without truncating)
- `reclaim/scoreboard.py` — every aggregate restricted to the seeded population, plus a `user_*` bucket
- `reclaim/verify.py` — two new checks
- `reclaim/api/app.py` — six new routes
- `reclaim/cli.py` — `reclaim evidence [--only ...]`
- `ui/src/app/page.tsx` — two new tabs
- `ui/src/components/Dashboard.tsx` — the "Submitted from the dashboard" card
- `ui/src/lib/api.ts` — types

### Routes

```
POST /api/sandbox/preview      submission -> trace, writes NOTHING
POST /api/sandbox/commit       submission -> real USR_ record + trace
POST /api/sandbox/reply        text       -> intent, gates, effect
POST /api/sandbox/guardrails   hypothetical -> 14 verdicts
POST /api/sandbox/reset        restore the committed snapshot
GET  /api/sandbox/presets      classify presets, reply presets, scenarios
GET  /api/evidence             committed measurements
```

---

## 3. The design rules that must not be broken

**Preview writes nothing, and needs no scratch database.** Diagnosis, the policy
table and the gate are pure; the gate's context is a READ. `_sandbox_preview_leaves_no_trace`
runs all five presets against the LIVE database and counts rows and the clock
either side. If a future change makes preview write, that check fails, which is
exactly when somebody needs to hear about it.

**Preview and commit share the decision path.** Preview calls `diagnose_batch`,
`decide` and `gate.run` — the same three functions the runner calls, in the same
order. Commit hands the record to `run_batch(only={id})`. Do not add a second
implementation of any decision.

**Visitor records are counted apart from every published figure.** `USR_` prefix,
`provenance.seeded_only()`. The scoreboard is the only place that needed it — see
§5 for why the other three suspected exposures were not real.

**The commit trace is read back off the audit log**, not reported from memory —
same principle as the scoreboard being recomputed from stored rows.

---

## 4. Traps that already bit, and will bite again

**`use_database` rebinds a module global.** `from .db import SessionLocal` on the
way into a `with use_database(...)` block binds the LIVE database. Read it through
the module inside the block: `dbmod.SessionLocal()`. This cost two debugging
rounds in `verify.py` and is commented in place there. It is also documented as
NOT thread-safe, which is why the sandbox preview deliberately does not use it.

**Two pytest processes destroy each other.** `tests/conftest.py` says so: one
SQLite file for the whole suite. Running a foreground `pytest` while a background
one is going produced 33 failures with `no such table: audit_log` and cost real
time chasing a bug that did not exist. **Never run two.**

**Uvicorn started without `--reload` does not pick up new routes.** Two rounds of
"the endpoint 404s" were a stale server. Restart it after adding routes.

**`promise_window` reads `ctx.extra["promised_for"]` and wants a `datetime`,**
not `promised_until` and not an ISO string. The simulator silently showed the rule
passing until this was fixed — a simulator that invents its own context key is
worse than no simulator, because it shows a rule passing that the batch would
have blocked.

**The console is cp1252 on this machine.** `print()` of a `₹` from a plain
`python -c` raises `UnicodeEncodeError`. Use `PYTHONIOENCODING=utf-8`. The CLI
itself handles it; only ad-hoc one-liners break.

---

## 5. Bugs found and fixed during the work

**Id reuse across a deleted record.** `next_user_id` read the high-water mark only
from `at_risk_records`, so deleting a committed record handed the next submission
the same id — grafting a stranger's words onto an older record's audit history,
which is precisely what that function's own docstring claimed it prevented. It now
reads the append-only audit log as well. *Caught by `test_the_record_id_space_survives_a_deleted_row`
on its first run.*

**The scoreboard leaked further than planned.** Not just money: `contacts`,
`interventions`, `escalations`, `webhooks_attributed`, `replies_read` and the
guardrail counts all aggregated over the live DB unfiltered. `contacts_per_recovery`
is a published figure (2.45 in DEPLOY.md), so a visitor's contacts would have
drifted it *while the headline held still* — the exact failure `_batch_is_reproducible`
exists to catch elsewhere.

**Three planned exposures did not exist,** and the plan overstated the risk on the
strength of B2's lesson without checking whether it applied:
- the reproducibility digest compares `generate(seed=42)` against itself, and
  `generate` is a pure function of its seed — nothing in a database can reach it;
- the ablation seeds scratch databases the same way;
- the baseline reads live rows but only looks them up by ids drawn from the seeded
  batch, so an unknown id contributes zero.

Likewise `/api/diagnosis` scores a freshly generated batch and cannot see the
database, so committed records can never reach diagnosis accuracy — which matters,
because §6 explains why they would score 100% by construction.

---

## 6. Known limitations — read before demoing

**A committed record has no independent ground truth.** The generator plants a
truth the diagnosers can be wrong about. A visitor typing "card expired" is not
concealing a different answer — their description *is* the fact of the matter. So
the outcome is drawn against the diagnosed cause, and a committed record therefore
can never be a diagnosis error. It is excluded from accuracy, but anyone reading
the code should know why rather than discovering it.

**The promise demo needs an API key to show its best path.** Without one,
`build_extractor()` returns nothing, the keyword reader takes over at confidence
0.50, the 0.60 floor refuses it, and every reply lands on `HUMAN`. That is the
correct degraded behaviour and it demonstrates rule 7 — but the `PROMISED` verdict,
which is the point of the mode, only appears with a key present.

**The commit trace has no `DETECT` card.** Preview shows DETECT → DIAGNOSE →
POLICY → GUARDRAILS; commit shows DIAGNOSE → DECIDE → GUARDRAIL → EXECUTE →
OUTCOME, because it is read off the audit log and the runner writes no DETECT row.
Honest, but the two strips do not have the same shape, which looks like a bug and
is not.

**The stage names differ between the two paths** for the same reason: preview
names them `DIAGNOSE L1` / `DIAGNOSE L2` / `POLICY` / `GUARDRAILS`; the audit log
uses `DIAGNOSE` / `DECIDE` / `GUARDRAIL`. Worth unifying if anyone touches this.

**`evidence/ablation.json` is machine-specific.** It was produced with a live key
on 2026-08-30 at commit `bad1a38`: +₹6,07,926, 38 fewer escalations, 0 harmful
actions, 38 API calls, 0% unanswered. The plan records ₹5,92,424 from an earlier
run — the difference is the model's own variance, which the project already
documents. Re-running will move it again. **This is expected, not a regression.**

**Nothing is committed to git.** All of Day 7 is unstaged.

---

## 6b. Three bugs found after the first commit, all now fixed

Found by driving the deployed site rather than by a test, which is worth
recording: the suite was green through all three.

**The reply reader never called the model.** `read_reply` called
`extractor.read(reply, today=...)` — a method that does not exist; the interface
is `CachedDiagnoser.__call__(*args)`, positional. The AttributeError was caught
by a broad `except Exception` and logged at debug, so every reply fell through to
the keyword matcher and the screen printed *"No model available and no
recognisable phrase"* while a working model sat right there. The same failure
CLAUDE.md warns about for guardrails — a thing that throws is a thing that gets
silently skipped — committed in a reader instead. There is now no try/except
there at all, because the extractor already never raises.

**The sandbox never hit the cache.** `api.app._llm()` builds a NEW diagnoser per
call and `CachedDiagnoser` keeps `self._cache` on the instance. Harmless for a
batch, where one instance serves the whole run; for the sandbox it meant every
preview was a live API call on a public endpoint, presets were never warm, and
identical input came back diagnosed differently — observed live: three identical
requests returned INSUFFICIENT_FUNDS, UNKNOWN, INSUFFICIENT_FUNDS. Fixed with a
process-wide diagnoser local to `sandbox.py`, deliberately NOT by changing
`_llm()`, so the batch endpoints keep the exact cache behaviour every published
figure was measured under.

**The cache key ignored `description`.** `signature()` keys on code, reason,
method, issuer, attempt and two booleans. Correct for the seeded batch, where
every record has a reason code and descriptions are canned per reason. Wrong for
the sandbox, where free text IS the signal: three unrelated sentences hashed
identically, so a warm cache would answer the second with the first one's
diagnosis — confidently and invisibly. Now keyed on description ONLY when the
reason is empty, which never happens in the seeded batch. Verified by partition
rather than assumed: the 120 payment records fall into the same 98 groups with
the same members before and after, so the ablation's API-call count cannot move.

The first bug hid the second, and the second hid the third. Fixing the cache
without fixing the key would have made the sandbox return a stale, confident,
wrong diagnosis for every submission after the first.

---

## 7. How to run it

```bash
reclaim serve                       # API + dashboard on one port
reclaim evidence                    # run every proof, commit the artifacts
reclaim evidence --only baseline    # just one
reclaim verify                      # 28 structural checks
pytest -q                           # 396 tests — ONE process only
```

The two new tabs are **Try it** (three modes: classify, reply, guardrails) and
**Evidence**. Committing a record from Try-it makes the "Submitted from the
dashboard" card appear on the Dashboard; it is hidden while `user_records` is 0.

---

## 8. What is deliberately not built (phase 5)

The full prompt drawer; the live-run hatch on the ablation; the attribution walk
rendered arrow by arrow; the forged-note toggle from 6b.3; the day-by-day scrubber
with its diff panel; and the tier 3–5 modes — detector sandbox, policy lookup,
rules-validation refusal, queue re-sort comparison, cohort lens, message composer,
dunning ladder.

Each is an endpoint plus a config object against components that now exist. That
was the point of building it this way, and it is why none of them are urgent.

---

## 9. Still open from earlier days

- **Re-capture live webhook proof into the repo.** Partly addressed: `evidence/`
  now exists and is the right home for it. The five verified deliveries lost with
  Render's `/tmp` still need re-capturing — pay one link, save the
  `simulated: false` JSON.
- **Rotate the Gemini API key** after the buildathon. Note that `reclaim evidence`
  spends quota on the ablation.
