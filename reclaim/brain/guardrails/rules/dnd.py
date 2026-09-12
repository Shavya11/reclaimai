"""Guardrail 3 - DND registry. TRAI compliance.

Blocks SMS and voice. Email and a payment link remain allowed, so a DND
customer is not unreachable, only unreachable by the channels TRAI governs.

The refusal routes to a person. It used to carry no next step at all - not a
deferral, not a stop, not a human - and the runner does nothing with a
violation like that, so the same three records were re-proposed on SMS and
re-refused on every tick for the whole run: thirty-six refusals for three
decisions, and the email the docstring promised never sent. DND does not
expire, so a deferral would loop the same way. A person who can email is the
only next step that actually ends it.
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
                   f"not permitted. Email or link only - routed to a person.",
            requires_human=True,
        )
