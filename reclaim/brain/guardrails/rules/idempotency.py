"""Guardrail 10 - idempotency. NO DOUBLE-CHARGING.

The real guarantee is the UNIQUE constraint on executed_actions.idempotency_key.
This check is the friendlier way of hitting it, and it is what puts the block in
the audit trail rather than surfacing as a database exception.

The key is derived from (record_id, attempt_number, action_type) as a property
of ProposedAction, so it cannot drift from the tuple it represents.
"""

from ....models import GuardrailViolation, ProposedAction
from ..base import GuardrailContext


class Idempotency:
    name = "idempotency"

    def check(self, action: ProposedAction, ctx: GuardrailContext):
        key = action.idempotency_key
        if key not in ctx.executed_keys:
            return None
        return GuardrailViolation(
            guardrail=self.name,
            reason=f"Idempotency key {key} has already executed. Replay blocked.",
            permanent=True,
        )
