"""Guardrail 11 - state validity. Do not chase money that already arrived."""

from ....enums import RecordState
from ....models import GuardrailViolation, ProposedAction
from ..base import GuardrailContext

# PROMISED belongs here, which looks wrong and is not.
#
# A promised record is still the agent's to work: it has to wake on the promised
# date, and it has to be chased when that date passes unpaid. What must not
# happen is contacting the customer in the meantime — and that is guardrail 14's
# job, deferring until the date, not this one's, which would block the record
# permanently and quietly kill every promise the moment it was made.
ACTIONABLE = {RecordState.AT_RISK.value, RecordState.IN_PROGRESS.value,
              RecordState.PROMISED.value}


class StateValidity:
    name = "state_validity"

    def check(self, action: ProposedAction, ctx: GuardrailContext):
        if ctx.record_state in ACTIONABLE:
            return None
        return GuardrailViolation(
            guardrail=self.name,
            reason=f"Record is {ctx.record_state}, not actionable.",
            permanent=True,
        )
