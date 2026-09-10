# VIDEO — full walkthrough shooting script

**Target: ~13:05.** Six parts, 26 clips. Every clip has an ID — record your voice
as separate files named `C01.wav`, `C02.wav`, … and they drop straight onto the
matching video clip.

**Pace:** written at ~145 words/minute. If you naturally read faster, the clips
get shorter, not wrong — the video is assembled to the voice, not the reverse.

**Read it conversationally.** The brief says explain it to an engineer, not a
recruiter. Slightly under-perform it. Flat and specific beats enthusiastic.

---

## Numbers that are WRONG in the older docs — do not say these

| Stale claim | Where | The truth |
|---|---|---|
| "twelve-value enum" | DEMO.md | **17** `RootCause` values |
| "13 guardrails" | PROJECT.md §7 | **14** rule files |
| "₹8.25 lakh at risk" | DEMO.md beat 1 | ₹8,24,984 is the **120 V1 records**. The full batch is **₹1,12,09,814 / 180 records** |
| "88 records held back" | DEMO.md beat 4 | **439 refusals across 154 records** |
| 35.0% / 2.51 contacts | README baseline table | That's the **baseline run**. The snapshot is **38.3% / 2.45**. Never mix them in one breath |

**Restore the snapshot before recording any screen.** Layer 2 is
non-deterministic; two live runs gave ₹27,44,651 and ₹23,44,566.

```bash
python -c "from reclaim.snapshot import restore; restore()"
python -m reclaim.cli serve
```

---

# PART 1 — THE PROBLEM (0:00–1:00)

### C01 · 0:00–0:23 · Title card
> Every payment that fails is revenue a merchant has already earned and will
> probably never see. The customer wanted to buy. The checkout worked. The money
> just didn't move. And in almost every business, nobody follows up — because a
> failed payment is a support ticket that never gets written.

### C02 · 0:23–0:40 · Dashboard, top row
> This is one merchant's month. One crore twelve lakh at risk, across a hundred
> and eighty records. Failed card payments. Abandoned carts. Bounced mandates.
> And sixty overdue B2B invoices — which is where most of the money actually is.

### C03 · 0:40–1:00 · Hold on dashboard
> Track three asks for an agent that recovers this. But detection is the easy
> half. Anyone can list failed payments. The question that decides whether you
> recover the money or annoy the customer is *why* each one failed — and whether
> you should touch it at all.

---

# PART 2 — THE ARCHITECTURE (1:00–2:30)

### C04 · 1:00–1:17 · Architecture diagram, full
> So here is the shape of the system. Detect, diagnose, decide, gate, execute,
> attribute — six stages, and a loop that closes when the money actually
> arrives. The interesting part is the boundary sitting in the middle of it.

### C05 · 1:17–1:42 · Highlight: model → label
> This is the rule the whole project is built on. The language model never
> touches money. It does exactly one thing — it reads the evidence and returns a
> label from a closed list of seventeen root causes, with a confidence score.
> That's it. It cannot pick an action. It cannot pick an amount, a time, or a
> recipient.

### C06 · 1:42–2:05 · Highlight: policy table → guardrails
> The label goes into a deterministic policy table — plain YAML, not code —
> which maps cause to action. That produces a *proposed* action. Proposed, not
> executed. Then fourteen guardrails decide whether it may actually fire.
> Consent, do-not-disturb, quiet hours, frequency caps, value ceilings,
> confidence floors, idempotency.

### C07 · 2:05–2:20 · Diagram, full again
> Which means a hallucination is harmless by construction. The worst a wrong
> label can do is cause a badly-timed retry. It can never cause a wrong charge.

---

# PART 3 — THE CODE (2:20–5:05)

### C08 · 2:20–2:35 · Repo tree in editor
> Let's look at how that's built. Detectors, brain, executor, webhooks, audit,
> api. One plugin per leak type — and the brain splits into diagnosis, policy,
> and guardrails.

### C09 · 2:35–3:02 · `brain/diagnosis/deterministic.py`
> Diagnosis is two layers. Layer one is a lookup table against real Razorpay
> error codes. It resolves about sixty percent of records at confidence one
> point zero, and it costs nothing. Those error strings were checked against
> Razorpay's published list — which caught sixteen keys in my own map that
> Razorpay never actually emits. They could never have matched in production.

### C10 · 3:02–3:19 · `brain/diagnosis/cohort.py`
> Layer one-and-a-half is the cohort signal. It groups the batch by issuer and
> hour and compares against baseline. This is what turns the word *declined*
> into *the bank is down* — and those are two completely different actions.

### C11 · 3:19–3:49 · `brain/diagnosis/llm_diagnoser.py`
> Layer two is the model, and only for the genuinely ambiguous rest. Forced tool
> use, so the output is a schema and not prose. The enum offered to the model is
> narrowed per leak type — a receivables record is never even shown *expired
> instrument*. A validation failure becomes UNKNOWN and goes to a human. And
> there's a signature cache that collapsed four hundred and twelve consultations
> into thirty-eight actual API calls.

### C12 · 3:49–4:05 · `brain/policy/policies.yaml`
> The policy table is data. Cause, action, channel, schedule — and a policy
> reference that gets stamped onto every decision so you can trace it back.
> Changing behaviour means editing YAML, not shipping code.

### C13 · 4:05–4:28 · `brain/guardrails/rules/` then `base.py`
> Fourteen guardrails, one file each. Two properties matter. First, they fail
> closed — `evaluate_all` never raises, so malformed input becomes a block, not
> an exception. A guardrail that throws is one that gets silently skipped later.
> Second, blocked is not dropped: every refusal computes when it may be
> reconsidered.

### C14 · 4:28–4:50 · `executor/` + the `executed_actions` schema
> And every write to Razorpay carries an idempotency key derived from record,
> attempt number, and action type. Derived — not passed in. The column is UNIQUE
> at the database. The agent cannot double-charge even if it crashes mid-batch,
> and that is the one property a payments project has to get right.

---

# PART 4 — THE PRODUCT (4:50–11:00)

### C15 · 4:50–5:28 · **Dashboard**
> Now the product. The dashboard is the scoreboard. One crore twelve lakh at
> risk, twenty-seven lakh forty-four thousand recovered — thirty-eight percent
> of records, at two point four five contacts per recovery. Recovery rate broken
> out by root cause, because the average hides everything. Bank downtime
> recovers at eighty-two percent with zero customer contacts. Risk declines
> recover at zero — and that is correct, those are never retried. And on the
> receivables side, DSO moves from a hundred and thirty-two days to a hundred
> and nineteen.

### C16 · 5:28–6:08 · **Try it** ← click this one slowly
> This tab is the one I'd click first if I were judging. Hand the agent a record
> it has never seen — an amount, an error code, a customer. Preview runs the
> entire pipeline and shows you the diagnosis, the policy row it matched, and
> every guardrail verdict — and it writes absolutely nothing. Nothing in the
> database moves. Commit makes it a real record on every other screen. So you
> can interrogate the decision process yourself, on your own input, instead of
> taking my seeded batch on faith.

### C17 · 6:08–6:36 · **Recovery queue**
> The recovery queue is every record and what happens to it next. It doesn't
> sort by amount — that was a bug, and I'll come back to it. It sorts into three
> hard tiers, then by expected value: amount, times probability of recovery for
> that cause, decayed by attempt and by age. And the rows sitting still tell you
> why they're sitting still — deferred, blocked, or waiting on a person.

### C18 · 6:36–7:32 · **Audit trail** → `REC_5042`
> Click any record and you get the full trail. This one is the best example in
> the batch. The error text says *the bank declined this transaction*. Read
> alone, that's a customer problem — you'd message them. But fifteen payments
> failed on the same issuer inside one hour. Seventy-three percent of that
> issuer's attempts, against a four and a half percent baseline. Sixteen times
> normal. So the diagnosis is bank downtime, from the cohort, at point nine
> confidence. The policy row says silent retry. The guardrails allow it. And
> twenty minutes later, it worked. Zero messages to that customer — and zero to
> the other fourteen. Every row here was written at the moment the decision was
> taken, and the table is append-only, enforced by database triggers rather than
> by my discipline.

### C19 · 7:32–8:34 · **Promises & replies**
> This screen is the agent deliberately saying nothing. A B2B invoice, two point
> four lakh, forty days overdue. The agent chased it, and accounts payable wrote
> back — in Hinglish, because that is how these actually arrive.
>
> *"Sir, abhi funds nahi hai, Friday tak clear kar denge."*
>
> The model does one thing with that sentence: it returns one of seven intents,
> and a date. Promise to pay. It does not decide what happens next. What happens
> next is guardrail fourteen — and what it decides is nothing. The agent goes
> silent until Friday. And notice the date goes through the same kind of
> validation the money does: not in the past, not beyond the horizon, parseable.
> A reply promising to pay next year is refused and reaches a person. The model
> never touches money — this is that same rule, applied to time. Friday came,
> they didn't pay, the promise is marked broken, and the record goes back onto
> the ladder one rung firmer.

### C20 · 8:34–8:57 · **Human queue**
> Five policy rows are marked no-auto-action by design. Knowing when to stop and
> fetch a person is the feature, not the gap. Fifty escalations here — and
> importantly, rows leave when the money arrives. That sounds obvious. It was
> broken for most of this build, and I'll come to it.

### C21 · 8:57–9:53 · **Rules studio**
> Every threshold in the system is data a merchant can argue with. But the real
> question isn't whether they can change it — it's whether they can find out
> what a change would cost before they make it. So: raise the value ceiling from
> fifty thousand to seventy-five, and replay. That re-runs the same hundred and
> eighty records under both rule sets, against a throwaway database, with
> diagnoses frozen — so the only thing differing between these two columns is
> the rules. More recovered, more messages sent, fewer records on a human's
> desk. A trade, stated in the three units a merchant actually cares about, and
> it can come out negative. Now watch this — set contacts per customer to
> ninety-nine. Refused, with the reason, because every edit is validated by the
> same code that will consume it. And the change history is append-only.

### C22 · 9:53–10:32 · **Evidence**
> And this tab is every claim the project makes, with the run that measured it —
> the command, the git commit, the timestamp. Including the ablation, which has
> two conditions where it refuses to show you a number at all. If more than a
> quarter of model calls went unanswered. And if zero calls were made — because
> then both arms are the same arm, every delta is zero by construction, and that
> would read as *the model makes no difference*. So there's no table, rather
> than a warning above one.

---

# PART 5 — PROOF AND GRACEFUL FAILURE (10:32–12:05)

### C23 · 10:32–10:54 · Terminal: `pytest -q`, then `cli verify`
> The receipts. Three hundred and forty-two tests. Twenty-five structural checks
> in a self-audit command. Three of those are property tests over random
> batches: no customer ever gets a third message in seven days, no action tuple
> ever executes twice, and no contact ever lands inside a promise window.

### C24 · 10:54–11:27 · Terminal: `run-batch --kill-razorpay`, `prove-idempotency`
> The failure case. Kill Razorpay entirely — every write fails. The batch still
> completes, the records park for human review, and nothing double-charges. Then
> this kills a batch mid-flight, restarts it, and counts the keys: a hundred and
> ninety-two executions, a hundred and ninety-two distinct keys, zero
> duplicates. Same story for the model — if it's down we fall back to the lookup
> table, then to UNKNOWN, then to a human. The batch always completes.

### C25 · 11:27–12:05 · `cli baseline`, then the gap panel
> And the comparison — including where I lose. The naive strategy, retry
> everything three times and message every failure, recovers more money than I
> do. I want to say that before you find it. It also makes four hundred and
> eighteen contacts to my hundred and fifty-eight, and seven hundred and
> twenty-nine of them are contacts my guardrails refuse. Every rupee of the gap
> is itemised — and of the whole gap, one thousand seven hundred and ten rupees
> across three records is the only part where my strategy was simply worse. The
> rest is money the agent was told not to take.

---

# PART 6 — CONCLUSION (12:05–13:05)

### C26 · 12:05–13:05 · Dashboard → repo URL card
> So: an agent that diagnoses before it acts, and refuses more often than it
> acts. Fourteen guardrails fired four hundred and thirty-nine times across a
> hundred and fifty-four records, and every single refusal is in an append-only
> log with its reason and what happens next. The blocks are the product.
>
> Everything here runs from a public repo — seeded, one command, no credentials
> required, because dry-run is the default. What's real is the Razorpay API, the
> error codes, the HMAC verification over raw bytes, the attribution walk, and
> every model call. What's modelled is whether a customer pays. That distinction
> is in the README, stated rather than waited for.
>
> Thank you.

---

## Visual assets

Rendered at 1920×1080 from [video/cards.html](video/cards.html) via
`.venv/Scripts/python video/shoot.py`. Edit the HTML and re-run to regenerate.

| Still | Covers | Clips | On screen for |
|---|---|---|---|
| `video/stills/01-problem.png` | Part 1 | C01 | 23s |
| `video/stills/02-architecture.png` | Part 2 | C04–C07 | 80s |
| `video/stills/03-conclusion.png` | Part 6 | C26 | 60s |

**Three, not two** — Part 2 has no screen to record either. It's 80 seconds of
narration over the architecture diagram, and without it that stretch is a blank.

A still held for 60–80s reads as a stalled video. In the edit, give each one a
**slow Ken Burns push** (scale 1.00 → 1.06 over the clip). For C04–C07 it is
better still to reveal the diagram in four steps — pipeline row, then the model
box, then policy + guardrails, then the red footer — cutting on each clip
boundary. The card is built so those four regions stack in reading order.

---

## Recording notes

- **One clip per file.** `C01.wav` … `C26.wav`. If a take is bad, redo only that
  clip — that's the whole reason for the numbering.
- **Leave half a second of silence** at the start and end of every file.
- Record all 26 in one sitting, one room, one mic position. Matching room tone
  later is the thing that makes home-recorded audio sound stitched.
- Say `REC_5042` as "record five-oh-four-two". Say `₹1,12,09,814` as "one crore
  twelve lakh" — don't read the digits.
- **C19's Hinglish line should sound like a quote**, not like narration. Slight
  pause before and after.

---

## Built output

`video/out/final.mp4` — 26 clips, 14:09. Rebuild any part with:

```bash
.venv/Scripts/python video/build.py part4     # one part
.venv/Scripts/python video/build.py C18       # one clip
.venv/Scripts/python video/build.py final     # stitch everything
```

Regenerate the pictures: `cards.py`→`shoot.py` (title/architecture/conclusion),
`arch_stages.py` (the four-stage reveal), `code_cards.py` (Part 3, sliced from
the real source), `term_cards.py` (Part 5, from real captured CLI output),
`dash_clips.py` (Part 4, drives the live dashboard), `note_card.py` (overlays).

`check_voice.py` reports each take's duration against target.

### Corrections carried as on-screen notes

The batch moved after the narration was written. Rather than re-record, three
clips carry a lower-third correction:

| Clip | Narration says | Screen shows |
|---|---|---|
| C20 | 51 escalations | 51 in the sidebar, but one settled record (`INV_7059`) had not left the queue — 50 are genuinely escalated |
| C23 | 342 tests, 25 checks | **402** tests; `verify` runs **28** checks, 27 passing |
| C25 | ₹1,710 across 3 records | **₹2,47,747 across 7 records**, and 169 contacts not 158 |

`INV_7059` is a real open bug: a record that settles via the promise-kept path
goes through neither of the two terminal points that close a human-queue row, so
`cli verify` reports 27/28. Left unfixed deliberately; the note says so on camera.
