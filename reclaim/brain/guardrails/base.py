"""Guardrail engine.

Guardrails know nothing about root causes, policies or models. They answer one
question: may THIS action fire, right now, against THIS customer?

Two properties are non-negotiable and both are tested:

  * evaluate_all NEVER raises. Malformed input blocks the action; it does not
    throw. A guardrail that throws is one somebody eventually wraps in
    `except: pass` and silently skips, and then it is not a guardrail.
  * ALL guardrails run, always. Collecting every violation rather than
    short-circuiting on the first is what makes the audit trail worth reading.

Blocked is not dropped. Every violation carries what happens next: a time to
retry, a human to route to, or a permanent stop.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from ...models import GuardrailResult, GuardrailViolation, ProposedAction


@dataclass
class GuardrailContext:
    """Everything a guardrail may look at. Deliberately a plain data bag — a
    guardrail that needs a database handle is a guardrail doing too much."""

    now: datetime
    autopilot_enabled: bool = True
    opted_out: bool = False
    on_dnd: bool = False
    contacts_last_7d: int = 0
    last_contact_at: datetime | None = None
    executed_keys: frozenset[str] = frozenset()
    record_state: str = "AT_RISK"
    record_age_days: float = 0.0
    diagnosis_confidence: float = 1.0
    actions_today: int = 0
    policy_max_attempts: int = 3
    extra: dict = field(default_factory=dict)


class Guardrail(Protocol):
    name: str

    def check(
        self, action: ProposedAction, ctx: GuardrailContext
    ) -> GuardrailViolation | None:
        """Return a violation to block, or None to allow."""
        ...


def evaluate_all(
    action: ProposedAction,
    ctx: GuardrailContext,
    guardrails: list[Guardrail] | None = None,
) -> GuardrailResult:
    """Runs ALL guardrails, collects ALL violations. Never raises. Never calls
    an LLM."""
    if guardrails is None:
        from .registry import REGISTRY

        guardrails = REGISTRY

    violations: list[GuardrailViolation] = []
    for rail in guardrails:
        try:
            found = rail.check(action, ctx)
        except Exception as exc:  # noqa: BLE001 — fail CLOSED, never open
            violations.append(GuardrailViolation(
                guardrail=getattr(rail, "name", rail.__class__.__name__),
                reason=f"Guardrail raised, blocking as a precaution: {exc!r}",
            ))
            continue
        if found is not None:
            violations.append(found)

    if not violations:
        return GuardrailResult(allowed=True)

    # A permanent stop outranks a deferral: there is no point rescheduling a
    # contact to someone who has opted out.
    permanent = any(v.permanent for v in violations)
    deferrals = [v.deferred_until for v in violations if v.deferred_until]
    return GuardrailResult(
        allowed=False,
        violations=violations,
        deferred_until=None if permanent else (max(deferrals) if deferrals else None),
        requires_human=any(v.requires_human for v in violations),
    )
