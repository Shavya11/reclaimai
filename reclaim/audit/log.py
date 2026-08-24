"""Append-only decision log. Every stage of every record writes here, including —
especially — the actions that were blocked."""

from datetime import datetime
from typing import Any

from sqlalchemy import select

from ..db import AuditLogRow, SessionLocal
from ..enums import Stage


def log(
    record_id: str,
    stage: Stage | str,
    outcome: str,
    reason: str,
    *,
    guardrail: str | None = None,
    payload: dict[str, Any] | None = None,
    deferred_until: datetime | None = None,
) -> int:
    row = AuditLogRow(
        record_id=record_id,
        stage=stage.value if isinstance(stage, Stage) else str(stage),
        outcome=outcome,
        guardrail=guardrail,
        reason=reason,
        payload=payload or {},
        deferred_until=deferred_until,
    )
    with SessionLocal() as session:
        session.add(row)
        session.commit()
        return row.id


def timeline(record_id: str) -> list[AuditLogRow]:
    """One record's full history, oldest first. This is demo beat #3."""
    with SessionLocal() as session:
        stmt = (
            select(AuditLogRow)
            .where(AuditLogRow.record_id == record_id)
            .order_by(AuditLogRow.at, AuditLogRow.id)
        )
        return list(session.scalars(stmt))
