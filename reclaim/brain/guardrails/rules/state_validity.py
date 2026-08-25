"""Guardrail 11 - state validity. Do not chase money that already arrived."""

from ....enums import RecordState
from ....models import GuardrailViolation, ProposedAction
from ..base import GuardrailContext

ACTIONABLE = {RecordState.AT_RISK.value, RecordState.IN_PROGRESS.value}


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
