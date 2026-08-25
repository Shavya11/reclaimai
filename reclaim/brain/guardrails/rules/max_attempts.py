"""Guardrail 5 - attempt cap.

Two ceilings: the policy row's own max, and a global hard cap no policy row can
raise. The global cap exists so a misconfigured YAML row cannot authorise
harassment.
"""

from ....models import GuardrailViolation, ProposedAction
from ...rules import threshold
from ..base import GuardrailContext


class MaxAttempts:
    name = "max_attempts"

    def check(self, action: ProposedAction, ctx: GuardrailContext):
        hard_cap = int(threshold("max_attempts", "global_hard_cap", default=3))
        if action.attempt_number > hard_cap:
            return GuardrailViolation(
                guardrail=self.name,
                reason=f"Attempt {action.attempt_number} exceeds the global hard "
                       f"cap of {hard_cap}.",
                permanent=True,
            )
        if action.attempt_number > ctx.policy_max_attempts:
            return GuardrailViolation(
                guardrail=self.name,
                reason=f"Attempt {action.attempt_number} exceeds the policy max "
                       f"of {ctx.policy_max_attempts}.",
                permanent=True,
            )
        return None
