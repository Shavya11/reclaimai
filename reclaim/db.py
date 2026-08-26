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
    # outcome is what OUR side did (EXECUTED / FAILED); result is what the
    # CUSTOMER did (RECOVERED / NO_RESPONSE), learned later from a webhook.
    # Keeping them apart matters: the frequency cap counts executions, and
    # folding a customer's reply into the same column would silently erase the
    # contact history guardrail #7 depends on.
    result: Mapped[str | None] = mapped_column(String(32))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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


class AppStateRow(Base):
    """Tiny key/value store. Holds the demo clock, so `tick` can advance time
    across separate CLI invocations and across an API process restart — a
    salary-window retry is a month away, and nobody watches a demo for a month.
    """

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(48), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WebhookEventRow(Base):
    """Razorpay retries webhooks, and a retry can arrive while the first is
    still in flight. UNIQUE(event_id) makes handling idempotent at the database
    rather than by a check-then-act that races itself."""

    __tablename__ = "webhook_events"
    __table_args__ = (UniqueConstraint("event_id", name="uq_webhook_event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(80), index=True)
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    razorpay_ref: Mapped[str | None] = mapped_column(String(64), index=True)
    record_id: Mapped[str | None] = mapped_column(String(64), index=True)
    amount: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[str] = mapped_column(String(32))
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, index=True
    )


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


def _ensure_sqlite_dir() -> None:
    """Create the database's parent directory if it is missing.

    SQLite will not create one, and the error it raises instead is
    `unable to open database file` with no mention of the path — which on a
    fresh host, where the only difference from a working machine is a directory
    nobody thought to create, is a genuinely awful way to spend an evening.
    """
    from pathlib import Path

    url = str(settings.database_url)
    if not url.startswith("sqlite:///"):
        return
    path = Path(url.replace("sqlite:////", "/").replace("sqlite:///", ""))
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)


def init_db() -> None:
    _ensure_sqlite_dir()
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for trigger in _APPEND_ONLY_TRIGGERS:
            conn.execute(text(trigger))


def reset_database() -> None:
    """Drop the file and rebuild it.

    Deleting rows is not an option: audit_log ABORTs on DELETE by design, and
    that is a property worth more than the convenience of a TRUNCATE. Anything
    wanting a clean slate wants a new database.

    Half a reset is worse than none. Clearing the mutable tables but leaving
    at_risk_records and audit_log behind leaves records ESCALATED and diagnoses
    already logged, and the next run behaves like a resumed one — which is how
    a passing test starts depending on which tests ran before it.
    """
    from pathlib import Path

    engine.dispose()
    url = str(settings.database_url)
    if url.startswith("sqlite:///"):
        path = Path(url.replace("sqlite:///", ""))
        if path.exists():
            try:
                path.unlink()
            except OSError:
                # Windows refuses to unlink a file another handle still holds,
                # and a leaked session is enough. Dropping the tables gets to
                # the same clean slate through the connection we already have.
                # DROP TABLE does not fire the row triggers, so append-only is
                # not being circumvented here - the table itself is going.
                Base.metadata.drop_all(engine)
    init_db()
