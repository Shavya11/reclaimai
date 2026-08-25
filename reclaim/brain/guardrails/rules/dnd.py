"""Guardrail 3 - DND registry. TRAI compliance.

Blocks SMS and voice. Email and a payment link remain allowed, so a DND
customer is not unreachable, only unreachable by the channels TRAI governs.
"""

from ....enums import Channel
from ....models import GuardrailViolation, ProposedAction
from ..base import GuardrailContext

BLOCKED_CHANNELS = {Channel.SMS, Channel.VOICE}


class DoNotDisturb:
    name = "dnd"

    def check(self, action: ProposedAction, ctx: GuardrailContext):
        if not ctx.on_dnd or action.channel not in BLOCKED_CHANNELS:
            return None
        return GuardrailViolation(
            guardrail=self.name,
            reason=f"Customer is on the DND registry; {action.channel.value} is "
                   f"not permitted. Email or link only.",
        )
