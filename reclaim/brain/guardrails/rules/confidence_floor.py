"""Guardrail 12 - confidence floor.

Ties the model's honesty to the system's behaviour. A diagnosis that admits it
is unsure cannot move money; it fetches a human instead. This is what makes
UNKNOWN a useful answer rather than a wasted one.
"""

from ....models import GuardrailViolation, ProposedAction
from ...rules import threshold
from ..base import GuardrailContext


class ConfidenceFloor:
    name = "confidence_floor"

    def check(self, action: ProposedAction, ctx: GuardrailContext):
        floor = float(threshold("confidence_floor", "minimum", default=0.6))
        if ctx.diagnosis_confidence >= floor:
            return None
        return GuardrailViolation(
            guardrail=self.name,
            reason=f"Diagnosis confidence {ctx.diagnosis_confidence:.2f} is below "
                   f"the {floor:.2f} floor. Routed to human review.",
            requires_human=True,
        )
