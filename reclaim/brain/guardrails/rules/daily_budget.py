"""Guardrail 9 - daily budget. Caps the blast radius of a bad run."""

from datetime import timedelta

from ....models import GuardrailViolation, ProposedAction
from ...rules import threshold
from ..base import GuardrailContext


class DailyBudget:
    name = "daily_budget"

    def check(self, action: ProposedAction, ctx: GuardrailContext):
        cap = int(threshold("daily_budget", "max_auto_actions_per_day", default=200))
        if ctx.actions_today < cap:
            return None
        tomorrow = (action.scheduled_for + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0)
        return GuardrailViolation(
            guardrail=self.name,
            reason=f"Daily budget of {cap} automated actions is exhausted "
                   f"({ctx.actions_today} used).",
            deferred_until=tomorrow,
        )
