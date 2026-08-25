"""Guardrail 4 - quiet hours. Contact only 09:00-20:00 IST.

Silent retries are exempt by design: they reach the issuer, not a sleeping
person, and delaying them costs real recovery for no benefit to anybody.
"""

from ....models import GuardrailViolation, ProposedAction
from ....timeutil import is_quiet_hours, next_contact_window
from ..base import GuardrailContext


class QuietHours:
    name = "quiet_hours"

    def check(self, action: ProposedAction, ctx: GuardrailContext):
        if not action.action_type.contacts_customer:
            return None
        when = action.scheduled_for
        if not is_quiet_hours(when):
            return None
        return GuardrailViolation(
            guardrail=self.name,
            reason=f"{when:%H:%M} IST falls inside quiet hours (20:00-09:00).",
            deferred_until=next_contact_window(when),
        )
