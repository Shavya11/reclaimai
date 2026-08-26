"""Guardrail 13 - freshness. A stopping rule.

Past a certain age, chasing a debt stops being collection and starts being
harassment. The record closes rather than cycling forever.
"""

from ....models import GuardrailViolation, ProposedAction
from ...rules import threshold
from ..base import GuardrailContext


class Freshness:
    name = "freshness"

    def check(self, action: ProposedAction, ctx: GuardrailContext):
        max_age = float(threshold("freshness", "max_age_days", default=90))
        if ctx.record_age_days <= max_age:
            return None
        return GuardrailViolation(
            guardrail=self.name,
            reason=f"Record is {ctx.record_age_days:.0f} days old, past the "
                   f"{max_age:.0f}-day limit. Closing.",
            permanent=True,
            closes_record=True,
        )
