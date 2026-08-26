"""Pydantic models at every boundary. Money is paise as int, never float."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import ActionType, Channel, LeakType, RecordState, RootCause
from .timeutil import to_ist


class _Base(BaseModel):
    model_config = ConfigDict(use_enum_values=False, extra="forbid")


class AtRiskRecord(_Base):
    """Deliberately generic. Payment-specific detail lives in raw_signals so that
    V2's overdue-invoice detector is a new leak_type, not a schema migration."""

    id: str
    leak_type: LeakType
    amount: int = Field(ge=0, description="paise")
    currency: str = "INR"
    counterparty_id: str
    source_ref: str
    detected_at: datetime
    due_at: datetime | None = None
    raw_signals: dict[str, Any] = Field(default_factory=dict)
    state: RecordState = RecordState.AT_RISK
    attempts: int = Field(default=0, ge=0)
    next_action_at: datetime | None = None

    @field_validator("detected_at", "due_at", "next_action_at")
    @classmethod
    def _ist(cls, v: datetime | None) -> datetime | None:
        return to_ist(v) if v else v


class Diagnosis(_Base):
    """Output of both diagnosis layers. The LLM fills exactly this shape via forced
    tool use; the deterministic layer fills it with confidence 1.0."""

    root_cause: RootCause
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    recoverable: bool
    evidence_used: list[str] = Field(default_factory=list)
    source: str = Field(description="deterministic | llm | fallback")


class ProposedAction(_Base):
    """Proposed, not executed. The guardrail engine decides whether it may fire."""

    record_id: str
    action_type: ActionType
    channel: Channel | None = None
    scheduled_for: datetime
    attempt_number: int = Field(ge=1)
    policy_ref: str = Field(description="e.g. FAILED_PAYMENT.BANK_DOWNTIME")
    rationale: str
    amount: int = Field(ge=0)

    @property
    def idempotency_key(self) -> str:
        """The single most important property in the system. Derived, never passed
        in, so it cannot drift from the tuple it is supposed to represent."""
        return f"{self.record_id}:{self.attempt_number}:{self.action_type.value}"


class GuardrailViolation(_Base):
    """`permanent` and `closes_record` are not the same thing, and conflating
    them is how a record gets killed for the wrong reason.

    `permanent` says THIS ACTION will never be allowed — do not reschedule it.
    An idempotency block is permanent in that sense: attempt 3 of REC_9 will
    never fire twice. The record itself is very much alive and moves on to
    attempt 4.

    `closes_record` says THE RECORD IS DONE — nobody will chase this money
    again. Opting out closes it. So does age, and so does exhausting the
    attempt cap. Treating an idempotency block as one of those would close
    every record the moment it successfully did anything.
    """

    guardrail: str
    reason: str
    deferred_until: datetime | None = None
    requires_human: bool = False
    permanent: bool = False
    closes_record: bool = False


class GuardrailResult(_Base):
    """Blocked is not dropped. A blocked action carries either a time to retry,
    a human to route to, or a permanent stop."""

    allowed: bool
    violations: list[GuardrailViolation] = Field(default_factory=list)
    deferred_until: datetime | None = None
    requires_human: bool = False

    @property
    def blocking_guardrails(self) -> list[str]:
        return [v.guardrail for v in self.violations]
