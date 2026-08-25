"""Guardrail 8 - value ceiling. Bounded authority.

Above the ceiling the agent stops and asks. Not because the decision is likely
to be wrong, but because the cost of being wrong stops being recoverable.
"""

from ....models import GuardrailViolation, ProposedAction
from ....money import format_inr
from ...rules import threshold
from ..base import GuardrailContext


class ValueCeiling:
    name = "value_ceiling"

    def check(self, action: ProposedAction, ctx: GuardrailContext):
        ceiling = int(threshold("value_ceiling", "requires_human_above",
                                default=5_000_000))
        if action.amount <= ceiling:
            return None
        return GuardrailViolation(
            guardrail=self.name,
            reason=f"{format_inr(action.amount)} exceeds the "
                   f"{format_inr(ceiling)} auto-action ceiling.",
            requires_human=True,
        )
