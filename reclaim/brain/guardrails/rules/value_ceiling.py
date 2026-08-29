"""Guardrail 8 - value ceiling. Bounded authority.

Above the ceiling the agent stops and asks. Not because the decision is likely
to be wrong, but because the cost of being wrong stops being recoverable.

The ceiling is PER LEAK TYPE, and V2 is what forced that. A single global number
is fine while every record is a consumer payment: ₹50,000 is a lot of authority
to hand an agent chasing a failed card, and seven records in a hundred exceed it.
Point the same number at B2B receivables, where a routine invoice is ₹2 lakh,
and it stops being a bound on authority and becomes an off switch — 48 of 60
invoices went to a human, which is not restraint, it is a queue nobody can work.

So the number is a judgement about a KIND of money, not about money. It is
tunable per leak type in guardrails.yaml, and it is one of the better arguments
for the rules studio: this is precisely the setting a merchant discovers is
wrong for them on their second day.
"""

from ....models import GuardrailViolation, ProposedAction
from ....money import format_inr
from ...rules import threshold
from ..base import GuardrailContext


class ValueCeiling:
    name = "value_ceiling"

    def check(self, action: ProposedAction, ctx: GuardrailContext):
        leak_type = action.policy_ref.partition(".")[0]
        ceiling = ceiling_for(leak_type)
        if action.amount <= ceiling:
            return None
        return GuardrailViolation(
            guardrail=self.name,
            reason=f"{format_inr(action.amount)} exceeds the "
                   f"{format_inr(ceiling)} auto-action ceiling for "
                   f"{leak_type.replace('_', ' ').lower()}.",
            requires_human=True,
        )


def ceiling_for(leak_type: str) -> int:
    """Per-leak-type ceiling, falling back to the global one.

    Falling back rather than requiring an entry is deliberate: a leak type
    somebody adds later inherits the strictest number in the file rather than no
    limit at all. A missing config row must never widen authority.
    """
    default = int(threshold("value_ceiling", "requires_human_above",
                            default=5_000_000))
    per_type = threshold("value_ceiling", "by_leak_type", default=None)
    if isinstance(per_type, dict) and leak_type in per_type:
        try:
            return int(per_type[leak_type])
        except (TypeError, ValueError):
            return default
    return default
