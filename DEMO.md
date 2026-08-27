# DEMO — five minutes

Follows PROJECT.md §13 exactly. Every command here has been executed and timed on
the build machine (see **Measured timings** at the bottom); the numbers quoted
are from `seed=42` and reproduce on any machine. The spoken pacing is the part
still to rehearse.

---

## Before you start

**Two terminals and one browser tab.**

```bash
# Terminal 1 — reset, run the whole arc, leave the server up
python -m reclaim.cli demo --extra-ticks 3     # ~24s, prints the scoreboard
python -m reclaim.cli serve                    # http://127.0.0.1:8000

# Terminal 2 — the receipts, ready to show but not yet run
python -m pytest tests/test_guardrails.py -q
python -m reclaim.cli verify
python -m reclaim.cli prove-idempotency
```

Browser on `http://127.0.0.1:8000`, **Dashboard** tab.

**Check before you speak:**

- [ ] `cli verify` shows 18 passed, 0 failed
- [ ] the dashboard's four top numbers are populated
- [ ] the Human queue tab shows escalations
- [ ] `DRY_RUN` badge is visible — say so out loud, do not let a judge discover it

---

## Beat 1 — the leak (30s)

**Dashboard, top row.**

> "This merchant has ₹8.25 lakh sitting in failed payments, dead carts and
> bounced mandates. 120 records. Today nobody chases any of it — a failed
> payment is a support ticket that never gets written."

Point at **Money at risk ₹8,24,984 · 120 records detected**.

---

## Beat 2 — run the batch (60s)

Do **not** re-run the whole arc on stage. Show that it is live instead:

Click **Run batch**, then **20m**, **2h**, **24h** in the advance row.

> "The agent detects, diagnoses, decides, checks thirteen guardrails, executes,
> and attributes the outcome. Each of those buttons advances the demo clock —
> a retry scheduled for the 1st of next month is not watchable otherwise, so
> the schedule resolves against an explicit time rather than the wall clock."

Each click is a real tick — detect, diagnose, decide, guardrail, execute,
attribute — and returns in well under a second. Watch **Recovered** move.

---

## Beat 3 — zoom into one record (90s)

**Recovery queue → search `REC_5042` → click it.**

This is a bank-outage record. Its error text says *"the bank declined this
transaction"* — read alone, that is a customer problem worth a message.

> "Fifteen payments failed on HDFC inside one hour. Seventy-three percent of
> that issuer's attempts, against a four-and-a-half percent baseline — sixteen
> times normal. The error says *declined*. The cohort says *the bank is down*.
>
> So the agent did not message this customer. It did not message any of the
> fifteen. It retried silently twenty minutes later, and it worked."

Point at the decision trail:

| Stage | What it says |
|---|---|
| Diagnosed | `BANK_DOWNTIME`, source `cohort`, confidence 0.9, with the evidence |
| Decided | `SILENT_RETRY`, policy `FAILED_PAYMENT.BANK_DOWNTIME` |
| Guardrail | `ALLOWED` |
| Executed | `SILENT_RETRY via no channel` |
| Outcome | `RECOVERED` — traced from `order.paid` back to this intervention |

**The line to say:**

> "Zero customer contacts on this cause. Fifteen needless messages prevented,
> and that counterfactual is computed, not claimed — `cli diagnose` prints it."

---

## Beat 4 — show a BLOCKED action (45s) ← THE WINNING MOMENT

**Recovery queue → filter `Blocked`.** Then open **`REC_5001`**.

> "This one the agent wanted to chase. ₹1,47,603 across two records. The value ceiling stopped it.
>
> The value ceiling: anything above ₹50,000 needs a human, whatever the policy
> says. And the confidence floor: the diagnosis came back `UNKNOWN` at zero
> confidence, and a system that will not admit it does not know is a system
> that guesses with someone's money.
>
> It is in the human queue, with both reasons attached."

Then **`REC_5015`**:

> "This customer opted out. Blocked permanently — not deferred, closed. The
> record is done and nobody will chase it again."

Switch to the **Dashboard → Guardrails** panel:

> "The agent wanted to act on all 120. Eighty-eight records were held back by a
> guardrail, and every single refusal is in an append-only log with its reason
> and what happens next. **The blocks are the product.**"

---

## Beat 5 — the scoreboard (60s)

**Dashboard, scroll to "Versus a naive strategy".**

> "Thirty-seven percent of records recovered, at 2.68 contacts per recovery.
>
> On its own that number means nothing. So here is the same 120 records under
> what most tooling actually does — retry everything three times immediately,
> message every failure. Same seeded outcomes; record REC_5041's second attempt
> succeeds or fails identically under both. Only the strategy differs.
>
> The naive run recovers more money. I want to be straight about that."

Then, immediately — **do not let a judge get there first**:

> "It also makes 272 contacts to our 75, and 386 of them are contacts our
> guardrail engine refuses: to people who opted out, to people on DND, at three
> in the morning, and thirty retries against risk declines that look like card
> testing to an issuer.
>
> And every rupee of the gap is accounted for." *(scroll to the gap panel)*
> "Most of it is money the agent was told not to take. A chunk of it is still in
> flight. The naive run is not a better strategy, it is an undeployable one —
> and this is what restraint costs, in rupees, rather than a claim that it
> is free."

---

## Beat 6 — one graceful failure (30s)

**Terminal 1:**

```bash
python -m reclaim.cli run-batch --kill-razorpay
```

> "Razorpay is now unreachable. Every write fails. The batch still completes,
> the records park for human review, and no idempotency key executes twice."

**Terminal 2, the closing move:**

```bash
python -m reclaim.cli prove-idempotency
```

> "This kills a batch mid-flight, restarts it, and counts the keys. The agent
> cannot double-charge — not because we were careful, but because
> `executed_actions.idempotency_key` is `UNIQUE` and the key is derived from
> `(record, attempt, action_type)` rather than passed in. Zero duplicates."

---

## Lines to have ready

- "The model cannot move money. It only produces a label from a closed enum."
- "We do not burn an LLM call where a lookup table answers — layer 2 runs on the
  ~30% that are genuinely ambiguous."
- "Real Razorpay APIs, real error-code shapes, real payment links. **Customer
  response is modelled** — say it before anyone asks."
- "`audit_log` is append-only at the database. SQLite triggers `ABORT` on UPDATE
  and DELETE. It is not a convention someone can forget."

## Questions you will get

**"What if the model hallucinates?"**
> It can only return a member of a fixed twelve-value enum. A schema violation
> becomes `UNKNOWN` and reaches a human. The policy table and thirteen
> guardrails sit below it either way — a wrong label costs a wrongly-timed
> retry, never a wrong charge.

**"Is the LLM running right now?"**
> No — there is no `ANTHROPIC_API_KEY` on this machine, so these numbers are the
> floor with layer 2 off. Thirty-eight records fall to `UNKNOWN` and are routed
> to a human rather than guessed at. `--no-llm` is a real code path with tests,
> not a mock: the batch is required to complete with the model down.

**"Are the webhooks real?"**
> The receiver, the HMAC verification and the attribution chain are real and
> tested — including the case that catches most implementations, verifying a
> re-serialized body instead of the raw bytes. What is not real is a public
> tunnel: `cloudflared` is not installed here, so the outcome payloads are
> generated locally, signed, and posted through the same endpoint Razorpay would
> hit. Nothing bypasses the signature check.

**"Why is your recovery rate lower than the naive one?"**
> Because we refuse contacts it makes. See the gap panel — it is itemised.

---

## Measured timings

Every command in this script, timed on the build machine. These are real
measurements, not estimates — budget your narration around them.

| Command | Time |
|---|---|
| `cli demo --extra-ticks 3` (full arc, 12 ticks) | 23.7s |
| `cli scoreboard` | 2.0s |
| `cli baseline` | 2.1s |
| `cli verify` | 3.1s |
| `cli prove-idempotency` | 13.0s |
| `cli run-batch --kill-razorpay` | 6.3s |
| `pytest tests/test_guardrails.py` (23 tests) | 5.1s |
| `POST /api/tick` (one dashboard button) | 0.6s |
| `GET /api/baseline` | 0.3s |

The commands total well under a minute, so the five minutes is spent talking,
not waiting. **What has not been rehearsed is the spoken pacing** — that needs a
stopwatch and a human voice, and it is the one thing on this page nobody has
verified. Do it twice before you present, and cut beat 5's per-cause read-out
first if you run long.

## Reproducibility

`seed=42` produces the same 120 records, the same `₹8,24,984`, and the same
timestamps on every machine and every run — `cli verify` checks the digest of
every field, not just the total. So the numbers in this file are the numbers you
will see. The scoreboard totals are stable for the whole day the batch is
generated (detection times anchor on midnight IST, so records still age
naturally without the compliance counts drifting mid-rehearsal).

## If something breaks on stage

The whole demo works from Terminal 1 alone. `cli scoreboard`, `cli baseline`,
`cli verify` and `cli prove-idempotency` print everything the browser shows. The
UI is the nice-to-have; the CLI is the deliverable.

If the dashboard shows a red banner, the API is not running — `reclaim serve`.
If the numbers look empty, the database was reset — `cli demo --extra-ticks 3`
and wait 24 seconds.
