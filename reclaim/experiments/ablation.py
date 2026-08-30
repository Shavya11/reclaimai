"""What is layer 2 actually worth?

We have always claimed the LLM earns its place because it took diagnosis
accuracy from 70.0% to 97.5%. **Accuracy is a proxy.** It says the labels are
right; it does not say the batch recovered more money, and it does not say a
person's day got shorter. Those are the claims that matter and we had never
measured either.

This measures them, on the population layer 2 actually serves. Across the 180
records of the V2 batch, layer 1 and the cohort signal between them resolve
about 110 — deterministic, already correct, and untouched here. Only the
remaining ~69 reach the model, so the question is narrow and answerable:

    On those, is calling the model better than not calling it?

The population is never hardcoded. It is read back from arm A's audit log as
"whichever records layer 2 actually answered", so it stays correct when the
batch grows or the deterministic map gets better.

With layer 2 off they take the documented fallback chain to UNKNOWN ->
`no_auto_action` -> a human. So the model's contribution shows up as money
recovered without anyone's help, and as rows that never landed on a desk.

**Why this design is cleaner than the obvious one, and free.** The temptation is
to compare model-handled records against rules-handled records. That measures
the ROUTER, not the model: records reach layer 2 *because they are hard*. Our
router already isolates the population and the batch is seeded, so both arms run
the SAME records under the SAME policy table and the SAME guardrails, and the
only thing that differs is whether layer 2 answers. No randomisation is needed
and the whole thing is reproducible from a seed.

**The cost side is counted, not just the win.** PROJECT.md already discloses
that layer 2 reads one of the three RISK_DECLINE records as INSUFFICIENT_FUNDS
at high confidence. Acting on that retries a card an issuer flagged, which is
the card-testing pattern that gets merchants fined. Sending a record to a human
is safe; sending one down that path is not. A report that counted only rescues
would be an advertisement.

**Both arms are handed the same self-curing customers.** Some people pay with no
prompting at all, and the simulator draws who from the planted cause on its own
stream — so the arm with layer 2 off still recovers money, and the delta is what
layer 2 added rather than what it looked like it added against a world where
nobody ever pays unaided. `money_arrived` is the honest headline: attributed and
organic together, which is what actually happened to the money. `recovered` is
the narrower figure the agent may claim.
"""

import logging
import os
import random
import tempfile
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..brain import rules
from ..db import use_database
from ..enums import RecordState, RootCause

log = logging.getLogger(__name__)

# Above this share of unanswered layer-2 calls the "with AI" arm is mostly the
# deterministic fallback, and the comparison is rules against rules wearing an
# agent label. PLAN.md 2.3 already records why this is not paranoia: a 429 is
# indistinguishable in the scoreboard from an honest refusal, so a rate-limited
# run still completes, still prints a lift and still looks reasonable.
VOID_THRESHOLD = 0.25

# Causes where any retry or contact is the wrong move, so acting on one because
# layer 2 mislabelled it is an active harm rather than a wasted message.
NEVER_ACT = frozenset({
    RootCause.RISK_DECLINE,
    RootCause.MANDATE_REVOKED,
    RootCause.POLICY_BLOCK,
})


class _CountingDiagnoser:
    """Wraps the real diagnoser to see how often it failed to answer.

    `CachedDiagnoser` swallows provider errors and returns None so a batch never
    dies — correct, and it is also why a failed run is invisible from the
    outside. A None here means the model did not answer, whether the cause was a
    rate limit, a timeout or a payload that failed validation. All three degrade
    the arm the same way, which is what the void check is asking about.

    Cache hits count as successful calls, because they are: the answer came from
    the model, once.
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls = 0
        self.failures = 0

    @property
    def available(self) -> bool:
        return getattr(self._inner, "available", self._inner is not None)

    def __call__(self, *args):
        self.calls += 1
        answer = self._inner(*args)
        if answer is None:
            self.failures += 1
        return answer

    @property
    def api_calls(self) -> int:
        """Requests that actually left the process.

        Distinct from `calls`, which counts every time the layer was consulted —
        most of those are cache hits across the arc's ticks. Reporting the
        larger number as API usage would overstate what layer 2 costs by about
        six times.
        """
        return int(getattr(self._inner, "calls", 0))

    @property
    def failure_rate(self) -> float:
        return self.failures / self.calls if self.calls else 0.0


@dataclass
class RecordOutcome:
    """What became of one record in one arm."""

    record_id: str
    amount: int = 0
    state: str = ""
    cause: str | None = None
    source: str | None = None
    recovered: int = 0
    contacts: int = 0
    queued: bool = False

    @property
    def is_recovered(self) -> bool:
        return self.state == RecordState.RECOVERED.value

    @property
    def organic(self) -> int:
        """Money that arrived on a record no intervention of ours can claim.

        Recovered with nothing attributed means the customer paid unprompted.
        Kept apart from `recovered` for the same reason the scoreboard keeps
        them apart, and summed with it only where the question is what actually
        happened to the money rather than who caused it.
        """
        return self.amount if (self.is_recovered and not self.recovered) else 0

    @property
    def money_arrived(self) -> int:
        return self.recovered + self.organic


@dataclass
class Arm:
    label: str
    outcomes: dict[str, RecordOutcome] = field(default_factory=dict)
    calls: int = 0
    failures: int = 0
    api_calls: int = 0

    @property
    def failure_rate(self) -> float:
        return self.failures / self.calls if self.calls else 0.0


def _bootstrap_ci(
    paired: list[tuple[float, float]],
    *,
    iterations: int = 2000,
    seed: int = 20260829,
) -> tuple[float, float]:
    """95% interval on a paired difference, by resampling records.

    Records are resampled as PAIRS because both arms saw the same record. The
    unpaired version would treat the decisions the two arms make identically as
    independent evidence and report a tighter interval than the data earns.

    Stdlib only. Pulling in scipy for one percentile would be a dependency a
    reader has to install to check our arithmetic.
    """
    if not paired:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(paired)
    diffs = []
    for _ in range(iterations):
        total = 0.0
        for _ in range(n):
            a, b = paired[rng.randrange(n)]
            total += a - b
        diffs.append(total / n)
    diffs.sort()
    lo = diffs[int(0.025 * len(diffs))]
    hi = diffs[min(len(diffs) - 1, int(0.975 * len(diffs)))]
    return (round(lo, 4), round(hi, 4))


def _snapshot() -> dict[str, RecordOutcome]:
    """Read one arm's results out before its scratch database is deleted."""
    from sqlalchemy import desc

    from ..db import (
        AtRiskRecordRow, AuditLogRow, HumanQueueRow, InterventionRow,
        SessionLocal,
    )
    from ..enums import Stage

    out: dict[str, RecordOutcome] = {}
    with SessionLocal() as session:
        for row in session.query(AtRiskRecordRow).all():
            out[row.id] = RecordOutcome(record_id=row.id, amount=row.amount,
                                        state=row.state)

        # Newest diagnosis wins, matching how the audit trail is read elsewhere.
        for row in (session.query(AuditLogRow)
                    .filter(AuditLogRow.stage == Stage.DIAGNOSE.value)
                    .order_by(desc(AuditLogRow.id)).all()):
            item = out.get(row.record_id)
            if item is None or item.cause is not None:
                continue
            item.cause = row.outcome
            item.source = str((row.payload or {}).get("source") or "")

        for row in session.query(InterventionRow).all():
            item = out.get(row.record_id)
            if item is None:
                continue
            if row.outcome == "EXECUTED" and row.channel:
                item.contacts += 1
            if row.result == "RECOVERED":
                item.recovered += row.recovered_amount or 0

        for row in (session.query(HumanQueueRow)
                    .filter(HumanQueueRow.resolved_at.is_(None)).all()):
            item = out.get(row.record_id)
            if item is not None:
                item.queued = True
    return out


def _seed_rules() -> None:
    """Copy the LIVE rules into the scratch database.

    Seeding from the shipped YAML would silently discard a merchant's edits and
    measure layer 2 against a policy table nobody is running.
    """
    from ..db import GuardrailConfigRow, PolicyRuleRow, SessionLocal

    live_policies = rules.policies()
    live_guardrails = rules.guardrail_config()
    with SessionLocal() as session:
        for leak, table in live_policies.items():
            for cause, row in table.items():
                session.add(PolicyRuleRow(leak_type=leak, root_cause=cause,
                                          row=dict(row)))
        for name, config in live_guardrails.items():
            session.add(GuardrailConfigRow(name=name, config=dict(config)))
        session.commit()
    rules.reload()


def _run_arm(label: str, *, use_layer2: bool, seed: int | None,
             arc: list[str] | None) -> Arm:
    """One arc, in a scratch database that is deleted afterwards.

    The same isolation `whatif.py` uses and `verify.py` already checks: the real
    runner, the real gate, the real executor and the real settlement all run,
    into a database that is thrown away. Nothing is mocked and no write is
    suppressed.

    Unlike the what-if replay, diagnoses are NOT frozen. That replay freezes
    them because rules never affect DIAGNOSE; here diagnosis IS the independent
    variable, so freezing it would measure nothing.
    """
    from .. import clock
    from ..runner import DEMO_ARC, run_batch, tick

    counter = None
    if use_layer2:
        from ..brain.diagnosis.llm_diagnoser import LLMDiagnoser

        llm = LLMDiagnoser()
        if not llm.available:
            from ..brain.diagnosis.gemini_diagnoser import GeminiDiagnoser

            llm = GeminiDiagnoser()
        if llm.available:
            counter = _CountingDiagnoser(llm)

    path = os.path.join(tempfile.gettempdir(),
                        f"reclaim_ablation_{uuid.uuid4().hex}.db")
    saved_offset = clock.offset().total_seconds()

    try:
        with use_database(f"sqlite:///{path}"):
            _seed_rules()
            clock.reset()

            run_batch(seed=seed, llm=counter, dry_run=True, extractor=None)
            for step in (arc if arc is not None else DEMO_ARC):
                tick(advance=step, seed=seed, llm=counter, dry_run=True,
                     extractor=None)

            arm = Arm(label=label, outcomes=_snapshot())
            if counter is not None:
                arm.calls = counter.calls
                arm.failures = counter.failures
                arm.api_calls = counter.api_calls
            return arm
    finally:
        rules.reload()
        _restore_clock(saved_offset)
        try:
            os.remove(path)
        except OSError:
            log.debug("scratch ablation database not removed: %s", path)


def _restore_clock(seconds: float) -> None:
    from .. import clock

    clock.set_offset(seconds)


@dataclass
class Ablation:
    """The comparison, or the refusal to make one."""

    with_ai: Arm
    without_ai: Arm
    population: list[str]
    truth: dict[str, RootCause]
    seed: int | None = None

    @property
    def void(self) -> bool:
        return bool(self.void_reason)

    @property
    def void_reason(self) -> str:
        """Empty when the run is a real ablation. Two ways it is not.

        No calls at all is the quieter failure and the more dangerous one: with
        no API key configured both arms are the same arm, every delta is zero,
        and the report reads as "layer 2 makes no difference" — a conclusion the
        run did nothing to earn.
        """
        if not self.with_ai.calls:
            return (
                "ABLATION VOID: layer 2 was never called, so both arms are the "
                "same arm. Configure a model key; every delta below would be "
                "zero by construction rather than by measurement."
            )
        if self.with_ai.failure_rate > VOID_THRESHOLD:
            return (
                f"ABLATION VOID: {self.with_ai.failure_rate:.0%} of layer-2 "
                f"calls went unanswered ({self.with_ai.failures} of "
                f"{self.with_ai.calls}), so the \"with AI\" arm is mostly the "
                f"deterministic fallback. These numbers are not an ablation and "
                f"must not be reported as one."
            )
        return ""

    def _paired(self, attr) -> list[tuple[float, float]]:
        pairs = []
        for rid in self.population:
            a = self.with_ai.outcomes.get(rid)
            b = self.without_ai.outcomes.get(rid)
            if a is None or b is None:
                continue
            pairs.append((float(attr(a)), float(attr(b))))
        return pairs

    def delta(self, attr) -> dict[str, Any]:
        pairs = self._paired(attr)
        with_total = sum(p[0] for p in pairs)
        without_total = sum(p[1] for p in pairs)
        lo, hi = _bootstrap_ci(pairs)
        n = len(pairs)
        return {
            "with_ai": round(with_total, 4),
            "without_ai": round(without_total, 4),
            "delta": round(with_total - without_total, 4),
            "per_record_ci_95": [lo, hi],
            "n": n,
        }

    def harmful(self) -> list[dict[str, Any]]:
        """Records layer 2 sent down a path the truth says is never safe.

        Not "records layer 2 got wrong" — a wrong label that produced no action
        costs nothing. This is the narrower and more serious set: the truth is a
        cause we must never act on, layer 2 called it something else, and an
        action actually fired.
        """
        out = []
        for rid in self.population:
            actual = self.truth.get(rid)
            if actual not in NEVER_ACT:
                continue
            got = self.with_ai.outcomes.get(rid)
            if got is None or got.cause == actual.value:
                continue
            if got.contacts == 0 and not got.is_recovered:
                continue
            out.append({
                "record_id": rid,
                "truth": actual.value,
                "diagnosed": got.cause,
                "contacts": got.contacts,
                "amount_paise": got.amount,
            })
        return out

    def as_dict(self) -> dict[str, Any]:
        if self.void:
            return {"void": True, "reason": self.void_reason,
                    "layer2_failure_rate": round(self.with_ai.failure_rate, 4),
                    "seed": self.seed}
        return {
            "void": False,
            "seed": self.seed,
            "population": len(self.population),
            "layer2_consulted": self.with_ai.calls,
            "layer2_api_calls": self.with_ai.api_calls,
            "layer2_failure_rate": round(self.with_ai.failure_rate, 4),
            "money_arrived_paise": self.delta(lambda o: o.money_arrived),
            "recovered_paise": self.delta(lambda o: o.recovered),
            "organic_paise": self.delta(lambda o: o.organic),
            "recovered_records": self.delta(lambda o: 1 if o.is_recovered else 0),
            "human_escalations": self.delta(lambda o: 1 if o.queued else 0),
            "contacts": self.delta(lambda o: o.contacts),
            "harmful_actions": self.harmful(),
            "headline": self.headline(),
        }

    def headline(self) -> str:
        """One sentence a person can read out, stating the cost as well as the
        gain. A version that reported only rupees would be marketing."""
        from ..money import format_inr

        if self.void:
            return self.void_reason

        money = self.delta(lambda o: o.money_arrived)["delta"]
        humans = self.delta(lambda o: 1 if o.queued else 0)["delta"]
        harm = len(self.harmful())

        sign = "+" if money >= 0 else "−"
        parts = [f"{sign}{format_inr(abs(int(money)))} recovered"]
        if humans:
            parts.append(f"{int(abs(humans))} "
                         f"{'fewer' if humans < 0 else 'more'} human "
                         f"escalation{'s' if abs(humans) != 1 else ''}")
        parts.append(f"{harm} harmful action{'s' if harm != 1 else ''}")
        return (f"Layer 2 on {len(self.population)} records: "
                + ", ".join(parts) + ".")


def run(*, seed: int | None = None, arc: list[str] | None = None) -> Ablation:
    """Both arms, and the comparison between them.

    The population is decided by arm A: whichever records layer 2 actually
    answered. Deciding it any other way — every record, or every record we
    *expect* to be ambiguous — would fold layer 1's work into the model's score.
    """
    from ..synthetic.generator import generate

    batch = generate(seed=seed if seed is not None else 42)

    with_ai = _run_arm("layer 2 on", use_layer2=True, seed=seed, arc=arc)
    without_ai = _run_arm("layer 2 off", use_layer2=False, seed=seed, arc=arc)

    population = sorted(
        rid for rid, o in with_ai.outcomes.items()
        if o.source == "llm" and rid in without_ai.outcomes
    )

    return Ablation(with_ai=with_ai, without_ai=without_ai,
                    population=population, truth=batch.truth, seed=seed)
