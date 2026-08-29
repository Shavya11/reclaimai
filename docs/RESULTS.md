# Results — what is layer 2 actually worth?

**Question:** we have always said the LLM earns its place because it took
diagnosis accuracy from 70.0% to 97.5%. Accuracy is a *proxy*. It says the
labels are right. It does not say the batch recovered more money, and it does
not say anybody's day got shorter.

This measures both, and reports what layer 2 costs as well as what it returns.

Reproduce with:

```bash
python -m reclaim.cli ablation --seed 42
```

---

## The design

Of the 180 records in the batch, layer 1 and the cohort signal between them
resolve about 110 — deterministically, correctly, and identically whether or not
a model is configured. Only the rest reach layer 2. So the question is narrow:

> On the records layer 2 actually answers, is calling it better than not?

Both arms run the **same records** from the **same seed**, through the same
policy table, the same fourteen guardrails, the same executor and the same
settlement. The only difference is whether layer 2 answers or the fallback chain
does. Each arm runs in its own scratch SQLite database that is deleted
afterwards; `verify.py` asserts the live database and the demo clock are
untouched.

**Why not compare model-handled records against rules-handled records?** Because
that measures the *router*, not the model — records reach layer 2 precisely
because they are hard. `recoup`, the strongest comparable project, had to
randomise within its hard cases to avoid exactly this. Our router already
isolates the population and the batch is seeded, so we get the clean comparison
for free and without randomisation.

**The population is not hardcoded.** It is read back from the audit log as
"whichever records layer 2 answered", so it stays correct as the batch grows or
the deterministic map improves.

---

## The result

**n = 69 records · seed 42 · `gemini-3.5-flash-lite` · 0% of calls unanswered**

| | layer 2 on | off | delta | 95% CI (per record) |
|---|---:|---:|---:|---|
| Recovered | ₹12,74,886 | ₹0 | **+₹12,74,886** | [+₹6,836, +₹32,737] |
| Records recovered | 23 | 0 | **+23** | [+0.23, +0.45] |
| Human escalations | 18 | 69 | **−51** | [−0.84, −0.64] |
| Contacts sent | 63 | 0 | +63 | [+0.74, +1.09] |
| Harmful actions | 0 | — | — | — |

**Cost: 38 API calls.** Layer 2 was consulted 412 times across the arc and made
38 requests, because the signature cache keys on what actually changes an answer
— error code, reason, method, issuer, attempt number — and 69 records collapse
to 38 distinct signatures. The nine-tenths of consultations that never left the
process are the cache doing the job it was built for.

Every interval excludes zero. They are bootstrap intervals over **paired**
resamples — both arms saw the same record, so treating the two decisions as
independent would report a tighter interval than the data earns.

**Headline:** on the 69 records it answers, layer 2 recovers ₹12.7L that the
fallback recovers none of, takes 51 records off a person's desk, and costs 63
customer contacts and 38 API calls to do it.

### The escalation number is the one to quote

With layer 2 off, all 69 records land in the human queue: no diagnosis means
`UNKNOWN`, which means `no_auto_action`, which means a person. **69 items is not
a queue, it is a backlog nobody works** — the same failure PLAN.md already
recorded when a mis-set value ceiling sent 48 of 60 invoices to a human.

With layer 2 on, 18 remain. That is a morning's work rather than a hiring
decision, and unlike the money figure it is **counted rather than modelled**.

### The cost side

63 contacts for 23 recoveries is **2.7 contacts per recovery** on this
population. That is well above the batch-wide figure, and it should be: these
are the records nothing else could diagnose.

**Zero harmful actions.** The check is deliberately narrow — not "records layer
2 labelled wrongly", which is cheap when nothing fires, but records where the
ground truth is a cause that must never be chased (`RISK_DECLINE`,
`MANDATE_REVOKED`, `POLICY_BLOCK`), layer 2 called it something else, **and an
action actually fired**. On this seed there were none.

---

## What this does not show

1. **The money delta is an upper bound.** The outcome simulator recovers a
   record only when an intervention fires, so the "off" arm scores exactly ₹0 on
   this population *by construction*. Real customers sometimes pay unprompted. A
   self-cure baseline — `recoup` reports organic recoveries of 46 vs 45 across
   arms and calls that balance what makes their comparison sound — is what would
   separate "the agent recovered this" from "this was arriving anyway". **We do
   not have one.** The escalation delta does not have this problem.

2. **The priors are stated estimates, not measured rates.** Per-cause recovery
   probabilities live in `reclaim/synthetic/outcomes.py`, published so they can
   be argued with. They are not calibrated against any real merchant.

3. **The run is reproducible only up to the model's own variance.** The seed
   fixes the batch, the rules and the arc; it does not fix the model. Two runs
   of this configuration gave identical recovered rupees (₹12,74,886), identical
   recovered records (23) and identical contacts (63), but escalations moved by
   one — 19 and then 18 — because a single borderline record was labelled
   differently the second time. The deterministic arm is exactly reproducible;
   the arm with the model is not, and quoting a single run as though it were
   would be overclaiming. Figures here are from the second run.

4. **One model, one prompt, one seed.** The claim is about this configuration.

5. **Ground truth is known by construction**, because the batch is synthetic.
   That is what makes the harmful-action check possible and also what limits it.

---

## The check that stops this flattering us

A rate-limited run still completes, still produces deltas, and still attaches
confidence intervals to them. On a deadline that is precisely the number that
gets published by accident.

So the harness refuses. If more than **25%** of layer-2 calls go unanswered, no
comparison is printed at all — not a warning above the table, *no table*, because
a reader who skims takes the table:

```
ABLATION VOID: 34% of layer-2 calls went unanswered (139 of 407), so the
"with AI" arm is mostly the deterministic fallback. These numbers are not an
ablation and must not be reported as one.
```

There is a second, quieter void condition: **zero calls**. With no API key
configured both arms are the same arm, every delta is zero, and the report would
read as "layer 2 makes no difference" — a conclusion the run did nothing to
earn. That is refused too.

PLAN.md §2.3 already recorded why this matters: a 429 is indistinguishable in
the scoreboard from the model honestly declining. Both surface as `UNKNOWN`.

`recoup` rejected three of its four ablation runs on this exact check, at 84%,
41% and 27% failure rates. Ours is the same idea, and it is tested — see
`tests/test_ablation.py`.

---

## Why we published this before knowing the answer

The measurement was built and committed before it was run, and the intention was
always to publish the result either way. If layer 2 had turned out to be worth
nothing, that would have been the finding and it would be in this file.

That is not hypothetical: `recoup` ran the equivalent experiment on their own
model, found it made outcomes **worse by 21 points**, and published it with the
interval. It is the most credible thing in their submission. A project that only
reports the experiments it wins has not run an experiment.
