"""Guardrail 2 - consent / opt-out. Legal, and non-negotiable.

Permanent, not deferred. Rescheduling a message to somebody who opted out is
still contacting somebody who opted out.
"""

from ....models import GuardrailViolation, ProposedAction
from ..base import GuardrailContext


class Consent:
    name = "consent"

    def check(self, action: ProposedAction, ctx: GuardrailContext):
        if not ctx.opted_out:
            return None
        if not action.action_type.contacts_customer:
            return None  # a silent retry reaches the bank, not the person
        return GuardrailViolation(
            guardrail=self.name,
            reason="Customer has opted out of contact. Blocked permanently.",
            permanent=True,
        )
