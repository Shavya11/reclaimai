"""The naive baseline — PROJECT.md §9, "MANDATORY".

    Retry everything three times, immediately. Message on every attempt.
    No diagnosis, no schedule, no guardrails, no stopping rules.

This is what most failed-payment tooling actually does, and it is the only
honest way to say whether any of the machinery above it earns its keep. **35%
alone means nothing. 35% against 19% means everything.**

The comparison is deliberately rigged in the baseline's favour in one respect:
it draws its coin flips from the same seeded stream ReclaimAI does, keyed on
`(record, attempt)`. Record REC_5041's second attempt succeeds or fails
identically under both strategies. Nothing separates the two runs except what
each one chose to do, when, and to whom — which is the entire claim.

Nothing here touches the database. The baseline is a counterfactual, not a run:
executing it would mean actually messaging 120 people three times each, which is
the behaviour the guardrails exist to prevent.
"""

import random
from dataclasses import dataclass, field
from typing import Any

from .enums import ActionType, RootCause
from .money import format_inr
from .synthetic import generate
from .synthetic.outcomes import probability
from .webhooks.attribution import RESULT_RECOVERED
from .timeutil import is_quiet_hours

# "Retry everything 3x immediately, message every failure." Straight from
# PROJECT.md §4.5 — not a strawman we invented to lose.
NAIVE_ATTEMPTS = 3


@dataclass
class BaselineResult:
    label: str = "Naive baseline"
    records: int = 0
    at_risk_paise: int = 0
    recovered_paise: int = 0
    recovered_records: int = 0
    # Customers who would have paid with or without the naive run. Counted the
    # same way and from the same draw as ours, because a baseline that gets
    # credited for self-cure while we are not is not a baseline.
    organic_paise: int = 0
    organic_records: int = 0
    contacts: int = 0
    contacts_to_opted_out: int = 0
    contacts_in_quiet_hours: int = 0
    contacts_to_dnd: int = 0
    retries_against_never_retry: int = 0
    customers_over_frequency_cap: int = 0
    by_root_cause: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def recovery_rate(self) -> float:
        return self.recovered_paise / self.at_risk_paise if self.at_risk_paise else 0.0

    @property
    def record_recovery_rate(self) -> float:
        return self.recovered_records / self.records if self.records else 0.0

    @property
    def contacts_per_recovery(self) -> float:
        return self.contacts / self.recovered_records if self.recovered_records else 0.0

    @property
    def compliance_breaches(self) -> int:
        """Every contact the guardrail engine would have refused. Not a
        subjective measure — each one maps to a numbered guardrail."""
        return (self.contacts_to_opted_out + self.contacts_in_quiet_hours
                + self.contacts_to_dnd + self.customers_over_frequency_cap)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "records": self.records,
            "at_risk_paise": self.at_risk_paise,
            "recovered_paise": self.recovered_paise,
            "organic_paise": self.organic_paise,
            "organic_records": self.organic_records,
            "organic_display": format_inr(self.organic_paise),
            "at_risk_display": format_inr(self.at_risk_paise),
            "recovered_display": format_inr(self.recovered_paise),
            "records_recovered": self.recovered_records,
            "recovery_rate": round(self.recovery_rate, 4),
            "record_recovery_rate": round(self.record_recovery_rate, 4),
            "contacts": self.contacts,
            "contacts_per_recovery": round(self.contacts_per_recovery, 2),
            "contacts_to_opted_out": self.contacts_to_opted_out,
            "contacts_to_dnd": self.contacts_to_dnd,
            "contacts_in_quiet_hours": self.contacts_in_quiet_hours,
            "customers_over_frequency_cap": self.customers_over_frequency_cap,
            "retries_against_never_retry": self.retries_against_never_retry,
            "compliance_breaches": self.compliance_breaches,
            "by_root_cause": self.by_root_cause,
        }


NEVER_RETRY_CAUSES = frozenset({RootCause.RISK_DECLINE, RootCause.MANDATE_REVOKED,
                                RootCause.POLICY_BLOCK})

FREQUENCY_CAP = 2  # mirrors guardrails.yaml; the baseline ignores it by design


def run(seed: int = 42) -> BaselineResult:
    batch = generate(seed=seed)
    customers = {c.id: c for c in batch.customers}
    result = BaselineResult()

    per_customer_contacts: dict[str, int] = {}

    for record in batch.records:
        cause = batch.truth[record.id]
        customer = customers.get(record.counterparty_id)

        result.records += 1
        result.at_risk_paise += record.amount
        line = result.by_root_cause.setdefault(
            cause.value, {"records": 0, "recovered": 0, "contacts": 0})
        line["records"] += 1

        if cause in NEVER_RETRY_CAUSES:
            result.retries_against_never_retry += NAIVE_ATTEMPTS

        for attempt in range(1, NAIVE_ATTEMPTS + 1):
            # Immediately means immediately: at the moment of detection,
            # whatever hour that is, whoever the customer is.
            result.contacts += 1
            line["contacts"] += 1
            per_customer_contacts[record.counterparty_id] = (
                per_customer_contacts.get(record.counterparty_id, 0) + 1)

            if customer is not None and customer.opted_out:
                result.contacts_to_opted_out += 1
            if customer is not None and customer.on_dnd:
                result.contacts_to_dnd += 1
            if is_quiet_hours(record.detected_at):
                result.contacts_in_quiet_hours += 1

            # Same stream, same key as settlement.py. See the module docstring.
            rng = random.Random(f"{seed}:{record.id}:{attempt}")
            p = probability(cause, action=ActionType.SEND_LINK,
                            attempt_number=attempt, in_salary_window=False)
            if rng.random() < p:
                result.recovered_records += 1
                result.recovered_paise += record.amount
                line["recovered"] += 1
                break
        else:
            # Three attempts spent and nothing came of them. If this customer
            # was going to pay anyway, the money still arrives — and it is no
            # more the naive run's doing than it is ours. Read from the same
            # `batch.self_cure` draw, so neither strategy can be credited with
            # a recovery the other is denied.
            if record.id in batch.self_cure:
                result.organic_records += 1
                result.organic_paise += record.amount

    result.customers_over_frequency_cap = sum(
        max(0, n - FREQUENCY_CAP) for n in per_customer_contacts.values())
    return result


# Why a record the baseline recovered was one we did not. Ordered: the first
# reason that applies wins, most-deliberate first, so "we refused on purpose"
# is never reported as "our strategy lost".
GAP_REASONS = (
    "refused_consent_or_dnd",
    "above_value_ceiling",
    "never_retry_policy",
    "undiagnosed_llm_offline",
    "still_open",
    "strategy",
)

GAP_LABELS = {
    "refused_consent_or_dnd": "customer opted out or on DND - contact refused",
    "above_value_ceiling": "above the value ceiling - routed to a human",
    "never_retry_policy": "risk decline / revoked mandate - never retried",
    "undiagnosed_llm_offline": "diagnosed UNKNOWN - layer 2 unavailable",
    "still_open": "still in flight - deferred, not abandoned",
    "strategy": "our strategy simply did worse here",
}


def gap_analysis(seed: int = 42) -> dict[str, Any]:
    """Decompose every rupee the naive strategy collected and we did not.

    Publishing a comparison we lose on is only defensible if we can say exactly
    where the loss came from. Most of it is money the agent was told not to
    take: contacts to people who opted out, amounts above the authority ceiling,
    causes the policy table refuses to retry. That is the cost of restraint, and
    stating it in rupees is more convincing than claiming it is free.
    """
    from .db import AtRiskRecordRow, CustomerRow, SessionLocal
    from .enums import RecordState
    from .scoreboard import diagnosed_causes

    batch = generate(seed=seed)
    customers = {c.id: c for c in batch.customers}
    naive = _per_record(seed=seed)
    causes = diagnosed_causes()

    ceiling = 5_000_000
    try:
        from .brain.rules import threshold
        ceiling = int(threshold("value_ceiling", "requires_human_above",
                                default=ceiling))
    except Exception:
        pass

    with SessionLocal() as session:
        states = {r.id: r.state for r in session.query(AtRiskRecordRow).all()}
        flags = {c.id: (c.opted_out, c.on_dnd)
                 for c in session.query(CustomerRow).all()}

    buckets: dict[str, dict[str, int]] = {
        r: {"records": 0, "paise": 0} for r in GAP_REASONS}
    total = {"records": 0, "paise": 0}

    for record in batch.records:
        if not naive.get(record.id):
            continue
        if states.get(record.id) == RecordState.RECOVERED.value:
            continue  # we got it too — no gap

        opted_out, on_dnd = flags.get(
            record.counterparty_id,
            (getattr(customers.get(record.counterparty_id), "opted_out", False),
             getattr(customers.get(record.counterparty_id), "on_dnd", False)))

        if opted_out or on_dnd:
            reason = "refused_consent_or_dnd"
        elif record.amount > ceiling:
            reason = "above_value_ceiling"
        elif batch.truth[record.id] in NEVER_RETRY_CAUSES:
            reason = "never_retry_policy"
        elif causes.get(record.id) == RootCause.UNKNOWN.value:
            reason = "undiagnosed_llm_offline"
        elif states.get(record.id) == RecordState.AT_RISK.value:
            reason = "still_open"
        else:
            reason = "strategy"

        buckets[reason]["records"] += 1
        buckets[reason]["paise"] += record.amount
        total["records"] += 1
        total["paise"] += record.amount

    return {
        "total": {**total, "display": format_inr(total["paise"])},
        "reasons": [
            {"reason": r, "label": GAP_LABELS[r], **buckets[r],
             "display": format_inr(buckets[r]["paise"])}
            for r in GAP_REASONS if buckets[r]["records"]
        ],
        "deliberate_paise": sum(
            buckets[r]["paise"] for r in
            ("refused_consent_or_dnd", "above_value_ceiling", "never_retry_policy")),
        "recoverable_with_layer_2_paise":
            buckets["undiagnosed_llm_offline"]["paise"],
        "still_open_paise": buckets["still_open"]["paise"],
    }


def _per_record(seed: int = 42) -> dict[str, bool]:
    """Which records the naive strategy would have recovered."""
    batch = generate(seed=seed)
    out: dict[str, bool] = {}
    for record in batch.records:
        cause = batch.truth[record.id]
        got = False
        for attempt in range(1, NAIVE_ATTEMPTS + 1):
            rng = random.Random(f"{seed}:{record.id}:{attempt}")
            if rng.random() < probability(cause, action=ActionType.SEND_LINK,
                                          attempt_number=attempt,
                                          in_salary_window=False):
                got = True
                break
        out[record.id] = got
    return out


@dataclass
class Comparison:
    baseline: BaselineResult
    ours: Any  # scoreboard.Scoreboard
    gap: dict[str, Any] = field(default_factory=dict)
    incremental: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        b, o = self.baseline, self.ours
        return {
            "baseline": b.as_dict(),
            "ours": o.as_dict(),
            "gap": self.gap,
            "incremental": self.incremental,
            "delta": {
                "recovery_rate_pp": round(
                    (o.recovery_rate - b.recovery_rate) * 100, 2),
                "record_recovery_rate_pp": round(
                    (o.record_recovery_rate - b.record_recovery_rate) * 100, 2),
                "recovered_paise": o.recovered_paise - b.recovered_paise,
                "contacts": o.contacts - b.contacts,
                "contacts_per_recovery": round(
                    o.contacts_per_recovery - b.contacts_per_recovery, 2),
                "compliance_breaches_avoided": b.compliance_breaches,
            },
        }


def compare(seed: int = 42) -> Comparison:
    from .scoreboard import compute

    return Comparison(baseline=run(seed=seed), ours=compute(),
                      gap=gap_analysis(seed=seed),
                      incremental=incremental(seed=seed))


def incremental(seed: int = 42) -> dict[str, Any]:
    """What each strategy is worth over doing nothing at all.

    Both headline figures overstate their strategy, and in the same way. A
    record recovered on day one from a customer who would have paid on day
    twenty is money that was always going to arrive; the strategy changed when
    it landed, not whether. Counting it as recovery credits the strategy with
    the customer's own intention.

    So the honest figure for either arm is what it collected from customers who
    would NOT have paid on their own — and this matters most for the naive run,
    which recovers more records and therefore absorbs more self-cures into its
    total. Its lead over us is smaller than its headline, and part of what looks
    like a lead is other people's money.

    Read from `batch.self_cure`, the same draw both arms settle against, so
    neither is measured against a world the other did not get.
    """
    from .db import InterventionRow, SessionLocal
    from .enums import RecordState

    batch = generate(seed=seed)
    would_pay_anyway = set(batch.self_cure)
    amounts = {r.id: r.amount for r in batch.records}

    naive_got = {rid for rid, got in _per_record(seed=seed).items() if got}
    naive_incremental = sum(amounts.get(r, 0)
                            for r in naive_got - would_pay_anyway)

    with SessionLocal() as session:
        ours_got = {
            row.record_id for row in session.query(InterventionRow)
            .filter(InterventionRow.result == RESULT_RECOVERED).all()}
    ours_incremental = sum(amounts.get(r, 0)
                           for r in ours_got - would_pay_anyway)

    return {
        "naive_recovered_paise": sum(amounts.get(r, 0) for r in naive_got),
        "naive_incremental_paise": naive_incremental,
        "naive_incremental_display": format_inr(naive_incremental),
        "naive_self_cures_claimed": len(naive_got & would_pay_anyway),
        "ours_recovered_paise": sum(amounts.get(r, 0) for r in ours_got),
        "ours_incremental_paise": ours_incremental,
        "ours_incremental_display": format_inr(ours_incremental),
        "ours_self_cures_claimed": len(ours_got & would_pay_anyway),
        "would_pay_anyway": len(would_pay_anyway),
    }
