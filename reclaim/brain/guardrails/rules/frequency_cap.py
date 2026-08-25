"""Guardrail 7 - frequency cap. The one most systems forget.

Counted per CUSTOMER, across every record they own - not per record. A customer
with four failed payments receives two messages, not four. Thinking at the
record level is exactly how well-meaning systems end up spamming people.
"""

from datetime import timedelta

from ....models import GuardrailViolation, ProposedAction
from ...rules import threshold
from ..base import GuardrailContext


class FrequencyCap:
    name = "frequency_cap"

    def check(self, action: ProposedAction, ctx: GuardrailContext):
        if not action.action_type.contacts_customer:
            return None
        cap = int(threshold("frequency_cap", "max_contacts", default=2))
        days = int(threshold("frequency_cap", "window_days", default=7))
        if ctx.contacts_last_7d < cap:
            return None
        return GuardrailViolation(
            guardrail=self.name,
            reason=f"Customer already received {ctx.contacts_last_7d} contacts in "
                   f"the last {days} days (cap {cap}), across all their records.",
            deferred_until=action.scheduled_for + timedelta(days=days),
        )
