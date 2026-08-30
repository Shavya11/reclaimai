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
| **Money arrived** | ₹21,02,354 | ₹15,09,930 | **+₹5,92,424** | [+₹2,378, +₹16,480] |
| — of which ours | ₹7,44,019 | ₹0 | +₹7,44,019 | [+₹3,997, +₹18,940] |
| — arrived anyway | ₹13,58,335 | ₹15,09,930 | **−₹1,51,595** | [−₹5,987, −₹3] |
| Records recovered | 32 | 16 | **+16** | [+0.13, +0.35] |
| Human escalations | 15 | 53 | **−38** | [−0.67, −0.43] |
| Contacts sent | 58 | 0 | +58 | [+0.65, +1.01] |
| Harmful actions | 0 | — | — | — |

**Read the third row before the second.** Layer 2 recovers ₹7,44,019 that the
fallback recovers none of — but ₹1,51,595 of that was going to arrive without
anyone doing anything, and the agent got there first. Netting the two is the
only figure worth quoting: **+₹5,92,424**, which is less than half what the same
experiment reported before self-cure existed.

That correction is the entire reason for building the baseline. The earlier
version of this file said the money delta was an upper bound; it was, by about
2×.

**Cost: 38 API calls.** Layer 2 was consulted 412 times across the arc and made
38 requests, because the signature cache keys on what actually changes an answer
— error code, reason, method, issuer, attempt number — and 69 records collapse
to 38 distinct signatures. The nine-tenths of consultations that never left the
process are the cache doing the job it was built for.

Every interval excludes zero. They are bootstrap intervals over **paired**
resamples — both arms saw the same record, so treating the two decisions as
independent would report a tighter interval than the data earns.

**Headline:** on the 69 records it answers, layer 2 adds ₹5,92,424 net of what
would have arrived anyway, takes 38 records off a person's desk, and costs 58
customer contacts and 38 API calls to do it.

### The escalation number is the one to quote

With layer 2 off, 53 of the 69 land in the human queue: no diagnosis means
`UNKNOWN`, which means `no_auto_action`, which means a person. (The other 16 pay
unprompted and close themselves.) **53 items is not a queue, it is a backlog
nobody works** — the same failure PLAN.md already recorded when a mis-set value
ceiling sent 48 of 60 invoices to a human.

With layer 2 on, 15 remain. That is a morning's work rather than a hiring
decision, and unlike the money figure it is **counted rather than modelled** —
no self-cure assumption touches it.

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

1. **The self-cure rates are the least defensible numbers here.** 34 of 180
   customers pay unprompted in this world, drawn per cause from `SELF_CURE` in
   `reclaim/synthetic/outcomes.py`. The *ordering* is arguable — an outage
   clears itself and the customer simply tries again, a dead card does not — but
   the magnitudes are estimates, and every figure above moves directly with
   them. Raise them and layer 2 looks worse; lower them and it looks better.
   Nothing here measured them.

2. **The recovery priors are stated estimates too**, published in the same file
   so they can be argued with, and not calibrated against any real merchant.

3. **The run is reproducible only up to the model's own variance.** The seed
   fixes the batch, the self-cure draw, the rules and the arc; it does not fix
   the model. Repeat runs agree on the money and the record counts and have
   moved escalations by one, because a single borderline record was labelled
   differently. The arm without the model is exactly reproducible; the arm with
   it is not, and quoting one run as though it were would be overclaiming.

4. **One model, one prompt, one seed.** The claim is about this configuration.

5. **Ground truth is known by construction**, because the batch is synthetic.
   That is what makes the harmful-action check possible and also what limits it.

---

## The self-cure baseline

A large share of failed payments recover with no intervention at all. A
simulator that does not model that asserts the opposite — that nobody ever pays
unless chased — and every agent measured inside it wins by construction.

34 of the 180 customers pay unprompted here, drawn per cause from `SELF_CURE`
and dated within 30 days of detection. Three properties make it usable:

- **Keyed on the planted cause, never on a diagnosis.** Whether somebody pays is
  a fact about them; what we decided their error code meant cannot change it.
  That is what makes the draw identical in every arm.
- **Its own random stream, drawn last.** `Random(seed + 2)`, after every other
  draw, and stored on the batch rather than on a record — so the reproducibility
  digest and every published figure are untouched by it.
- **Nobody who cannot pay pays anyway.** A card the issuer blocked will be
  blocked again; a revoked mandate has nothing left to debit. Those causes are
  rated zero and `cli verify` asserts none of them ever self-cures.

**An unprompted payment is never credited to us.** This turned out to be the
hard part. Our executor writes `notes.record_id` on everything it mints, so a
note is proof the money came through us — and the first version of this forged
that note on organic payments, which meant attribution adopted them and the
agent was credited with 23 recoveries it had not caused. An unprompted payment
now arrives against the merchant's *original* order reference and carries no
note of ours, so nothing can claim it. It is recorded as `ORGANIC`: the money
arrived, and none of it is the agent's.

### What it changed

| | before | after |
|---|---:|---:|
| Written off as unrecoverable | ₹14,06,868 | ₹13,70,992 |
| Arrived without us | not modelled | ₹25,90,748 |
| Naive baseline, headline | ₹47,58,234 | ₹47,58,234 |
| Naive baseline, **incremental** | not computable | **₹27,50,189** |
| Ours, **incremental** | not computable | **₹19,02,912** |

The naive strategy contacts everybody, so it absorbs more self-cures and claims
them: 21 of its recoveries would have arrived anyway, against 10 of ours. Net of
that, **roughly two thirds of its apparent lead is money that was coming
regardless.**

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
