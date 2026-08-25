"""Runs a whole batch of proposed actions through the guardrail engine.

The running state is the point. Guardrail 7 counts contacts per CUSTOMER across
every record they own, and guardrail 10 remembers what has already executed, so
neither can be evaluated one action at a time in isolation — they need a tally
that carries forward through the batch.

Produces the number the demo turns on: the agent wanted to take N actions and
was permitted M, with every refusal and its reason.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from ..config import settings
from ..enums import RecordState
from ..models import AtRiskRecord, Diagnosis, GuardrailResult, ProposedAction
from ..timeutil import now
from .guardrails import GuardrailContext, evaluate_all


@dataclass
class GateOutcome:
    action: ProposedAction
    result: GuardrailResult


@dataclass
class GateReport:
    outcomes: list[GateOutcome] = field(default_factory=list)
    blocked_by: Counter = field(default_factory=Counter)

    @property
    def proposed(self) -> int:
        return len(self.outcomes)

    @property
    def allowed(self) -> int:
        return sum(1 for o in self.outcomes if o.result.allowed)

    @property
    def blocked(self) -> int:
        return self.proposed - self.allowed

    @property
    def requiring_human(self) -> int:
        return sum(1 for o in self.outcomes if o.result.requires_human)

    @property
    def deferred(self) -> int:
        return sum(1 for o in self.outcomes
                   if not o.result.allowed and o.result.deferred_until)

    def as_dict(self) -> dict:
        return {
            "proposed": self.proposed,
            "allowed": self.allowed,
            "blocked": self.blocked,
            "deferred": self.deferred,
            "requiring_human": self.requiring_human,
            "blocked_by_guardrail": dict(self.blocked_by.most_common()),
        }


def run(
    records: list[AtRiskRecord],
    diagnoses: dict[str, Diagnosis],
    actions: list[ProposedAction],
    customers: dict[str, object],
    *,
    frm: datetime | None = None,
    executed_keys: set[str] | None = None,
    autopilot_enabled: bool | None = None,
) -> GateReport:
    frm = frm or now()
    executed = set(executed_keys or ())
    by_id = {r.id: r for r in records}
    autopilot = (settings.autopilot_enabled if autopilot_enabled is None
                 else autopilot_enabled)

    # Seeded from what was actually executed, so the seven-day window survives a
    # process restart. An in-memory-only tally makes guardrail #7 true per run
    # rather than per customer.
    from ..repository import contact_history

    prior_counts, prior_last = contact_history(frm)
    contacts: Counter = Counter(prior_counts)
    last_contact: dict[str, datetime] = dict(prior_last)
    actions_today = 0
    report = GateReport()

    for action in actions:
        record = by_id[action.record_id]
        customer = customers.get(record.counterparty_id)
        diagnosis = diagnoses[action.record_id]

        ctx = GuardrailContext(
            now=frm,
            autopilot_enabled=autopilot,
            opted_out=bool(getattr(customer, "opted_out", False)),
            on_dnd=bool(getattr(customer, "on_dnd", False)),
            contacts_last_7d=contacts[record.counterparty_id],
            last_contact_at=last_contact.get(record.counterparty_id),
            executed_keys=frozenset(executed),
            record_state=record.state.value,
            record_age_days=max(0.0, (frm - record.detected_at).total_seconds() / 86400),
            diagnosis_confidence=diagnosis.confidence,
            actions_today=actions_today,
            policy_max_attempts=3,
        )

        result = evaluate_all(action, ctx)
        report.outcomes.append(GateOutcome(action, result))

        if result.allowed:
            executed.add(action.idempotency_key)
            actions_today += 1
            if action.action_type.contacts_customer:
                contacts[record.counterparty_id] += 1
                last_contact[record.counterparty_id] = action.scheduled_for
        else:
            for violation in result.violations:
                report.blocked_by[violation.guardrail] += 1

    return report
