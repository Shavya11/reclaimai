"""Guardrail 14 - promise window. Somebody said they would pay on a date; until
that date passes, leave them alone.

This is a guardrail rather than a branch in the runner, and the choice is the
whole point. Expressed here it answers the same question every other guardrail
answers — may THIS action fire, right now, against THIS customer — so it fails
closed, it lands in the audit trail as a block with a reason a human can read,
it appears in the guardrail breakdown next to quiet hours and the frequency cap,
and any channel added later inherits it without knowing it exists. Special-cased
in the orchestrator it would have none of those properties.

Deferred, never permanent: the promise has a date, and the date is exactly when
the action becomes allowed again. A broken promise is not this guardrail's
business — `promises.settle_due` handles the lapse and hands the record back to
the ladder one rung further on.
"""

from datetime import timedelta

from ....models import GuardrailViolation, ProposedAction
from ...rules import threshold
from ..base import GuardrailContext


class PromiseWindow:
    name = "promise_window"

    def check(self, action: ProposedAction, ctx: GuardrailContext):
        if not action.action_type.contacts_customer:
            # A silent retry reaches the bank, not the person, and costs the
            # promise nothing. Chasing the money quietly while staying off
            # somebody's phone is precisely the distinction this system makes.
            return None

        promised_for = ctx.extra.get("promised_for")
        if promised_for is None:
            return None

        # A grace period after the date, so a promise made for Friday is not
        # dunned at one minute past midnight on Saturday. Collections norms, and
        # the difference between firm and unpleasant.
        grace = float(threshold("promise_window", "grace_hours", default=24))
        release = promised_for + timedelta(hours=grace)
        if action.scheduled_for >= release:
            return None

        return GuardrailViolation(
            guardrail=self.name,
            reason=f"Customer committed to pay by {promised_for:%Y-%m-%d}. "
                   f"Contact is held until {release:%Y-%m-%d %H:%M} "
                   f"({grace:.0f}h grace).",
            deferred_until=release,
        )
