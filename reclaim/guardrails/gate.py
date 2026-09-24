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

from reclaim.config import settings
from reclaim.enums import RecordState
from reclaim.models import AtRiskRecord, Diagnosis, GuardrailResult, ProposedAction
from reclaim.timeutil import now
from reclaim.guardrails import GuardrailContext, evaluate_all


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


def _policy_max_attempts(policy_ref: str, policy_for, default: int = 3) -> int:
    """The row's own ceiling. It was the literal 3 - true for most rows and
    quietly wrong for any row that said otherwise."""
    leak, _, cause = policy_ref.partition(".")
    row = policy_for(leak, cause) or {}
    try:
        return int(row.get("max_attempts", default))
    except (TypeError, ValueError):
        return default


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
    # The persisted value, not the process's: a flip made through the API has
    # to survive a restart, or the panic button un-presses itself.
    from reclaim.guardrails.killswitch import enabled as autopilot_persisted

    autopilot = (autopilot_persisted() if autopilot_enabled is None
                 else autopilot_enabled)

    # Seeded from what was actually executed, so the seven-day window survives a
    # process restart. An in-memory-only tally makes guardrail #7 true per run
    # rather than per customer.
    from reclaim.repository import actions_today as actions_already_today, contact_history
    from reclaim.rules import policy_for, threshold

    # The window the cap COUNTS over must be the window it claims. Editing
    # `window_days` in the rules studio used to change the deferral but not
    # the count - a 14-day deferral computed off a 7-day tally.
    window = int(threshold("frequency_cap", "window_days", default=7))
    prior_counts, prior_last = contact_history(frm, window_days=window)

    # One query for the whole batch. Guardrail 14 is evaluated per action, and
    # a promise lookup per action would be 180 round trips to answer a question
    # whose answer cannot change mid-batch.
    from reclaim.measure.promises import open_promises

    promised = open_promises(frm)

    contacts: Counter = Counter(prior_counts)
    last_contact: dict[str, datetime] = dict(prior_last)
    actions_today = actions_already_today(frm)
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
            policy_max_attempts=_policy_max_attempts(action.policy_ref, policy_for),
            extra={"promised_for": promised.get(action.record_id)},
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
