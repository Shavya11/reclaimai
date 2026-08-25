"""Guardrail 6 - cooldown. A minimum gap between contacts to one customer."""

from datetime import timedelta

from ....models import GuardrailViolation, ProposedAction
from ...rules import threshold
from ..base import GuardrailContext


class Cooldown:
    name = "cooldown"

    def check(self, action: ProposedAction, ctx: GuardrailContext):
        if not action.action_type.contacts_customer or ctx.last_contact_at is None:
            return None
        hours = float(threshold("cooldown", "hours_between_contacts", default=24))
        earliest = ctx.last_contact_at + timedelta(hours=hours)
        if action.scheduled_for >= earliest:
            return None
        return GuardrailViolation(
            guardrail=self.name,
            reason=f"Last contact was {ctx.last_contact_at:%Y-%m-%d %H:%M}; the "
                   f"{hours:.0f}h cooldown has not elapsed.",
            deferred_until=earliest,
        )
