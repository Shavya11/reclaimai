"""SQLAlchemy tables. Two invariants are enforced by the database itself rather
than by application discipline:

  * executed_actions.idempotency_key is UNIQUE  -> no double-charge, ever
  * audit_log rejects UPDATE and DELETE via triggers -> append-only, provably

Enforcing these in Python only would mean they hold until someone writes a bug.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings
from .timeutil import now


class Base(DeclarativeBase):
    pass


class AtRiskRecordRow(Base):
    __tablename__ = "at_risk_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    leak_type: Mapped[str] = mapped_column(String(32), index=True)
    amount: Mapped[int] = mapped_column(Integer)  # paise
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    counterparty_id: Mapped[str] = mapped_column(String(64), index=True)
    source_ref: Mapped[str] = mapped_column(String(64))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_signals: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(32), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CustomerRow(Base):
    """Frequency cap and consent are customer-level, not record-level. A customer
    with four failed payments gets two messages, not four."""

    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False)
    on_dnd: Mapped[bool] = mapped_column(Boolean, default=False)
    successful_payments_lifetime: Mapped[int] = mapped_column(Integer, default=0)
    last_successful_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InterventionRow(Base):
    """One attempt to recover one record. Outcome attribution walks back from a
    webhook to the intervention that caused it."""

    __tablename__ = "interventions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[str] = mapped_column(String(64), index=True)
    action_type: Mapped[str] = mapped_column(String(32))
    channel: Mapped[str | None] = mapped_column(String(32))
    policy_ref: Mapped[str] = mapped_column(String(128))
    attempt_number: Mapped[int] = mapped_column(Integer)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    razorpay_ref: Mapped[str | None] = mapped_column(String(64), index=True)
    outcome: Mapped[str | None] = mapped_column(String(32))
    recovered_amount: Mapped[int] = mapped_column(Integer, default=0)


class ExecutedActionRow(Base):
    """Guardrail #10. The UNIQUE constraint is the actual guarantee; the guardrail
    check is just a friendlier way of hitting it."""

    __tablename__ = "executed_actions"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_idempotency_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), index=True)
    record_id: Mapped[str] = mapped_column(String(64), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    action_type: Mapped[str] = mapped_column(String(32))
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    razorpay_ref: Mapped[str | None] = mapped_column(String(64))


class AuditLogRow(Base):
    """Append-only. Blocked actions are logged as loudly as executed ones — the
    blocks are the demo."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[str] = mapped_column(String(32), index=True)
    outcome: Mapped[str] = mapped_column(String(32))
    guardrail: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    deferred_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class HumanQueueRow(Base):
    """Escalations go to a review table, not the void."""

    __tablename__ = "human_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[str] = mapped_column(String(64), index=True)
    reason: Mapped[str] = mapped_column(Text)
    amount: Mapped[int] = mapped_column(Integer, default=0)
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


_APPEND_ONLY_TRIGGERS = (
    """
    CREATE TRIGGER IF NOT EXISTS audit_log_no_update
    BEFORE UPDATE ON audit_log
    BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
    BEFORE DELETE ON audit_log
    BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
    """,
)

engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@event.listens_for(engine, "connect")
def _enable_foreign_keys(dbapi_conn, _record):
    dbapi_conn.execute("PRAGMA foreign_keys=ON")


def init_db() -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for trigger in _APPEND_ONLY_TRIGGERS:
            conn.execute(text(trigger))
