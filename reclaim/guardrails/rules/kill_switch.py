"""Guardrail 1 - kill switch. The panic button: one flag stops everything."""

from reclaim.models import GuardrailViolation, ProposedAction
from reclaim.guardrails.base import GuardrailContext


class KillSwitch:
    name = "kill_switch"

    def check(self, action: ProposedAction, ctx: GuardrailContext):
        if ctx.autopilot_enabled:
            return None
        return GuardrailViolation(
            guardrail=self.name,
            reason="Autopilot is disabled. No action may fire.",
            requires_human=True,
        )
