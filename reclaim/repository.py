"""Persistence for records and customers. Kept separate from db.py so the table
definitions stay readable."""

from sqlalchemy import select

from .db import AtRiskRecordRow, CustomerRow, SessionLocal
from .enums import LeakType, RecordState
from .models import AtRiskRecord
from .timeutil import to_ist


def _to_model(row: AtRiskRecordRow) -> AtRiskRecord:
    return AtRiskRecord(
        id=row.id, leak_type=LeakType(row.leak_type), amount=row.amount,
        currency=row.currency, counterparty_id=row.counterparty_id,
        source_ref=row.source_ref, detected_at=row.detected_at, due_at=row.due_at,
        raw_signals=row.raw_signals or {}, state=RecordState(row.state),
        attempts=row.attempts, next_action_at=row.next_action_at,
    )


def save_batch(records, customers) -> int:
    with SessionLocal() as s:
        s.query(AtRiskRecordRow).delete()
        s.query(CustomerRow).delete()
        for c in customers:
            s.add(CustomerRow(
                id=c.id, email=c.email, phone=c.phone, opted_out=c.opted_out,
                on_dnd=c.on_dnd,
                successful_payments_lifetime=c.successful_payments_lifetime,
                last_successful_at=c.last_successful_at,
            ))
        for r in records:
            s.add(AtRiskRecordRow(
                id=r.id, leak_type=r.leak_type.value, amount=r.amount,
                currency=r.currency, counterparty_id=r.counterparty_id,
                source_ref=r.source_ref, detected_at=r.detected_at, due_at=r.due_at,
                raw_signals=r.raw_signals, state=r.state.value, attempts=r.attempts,
                next_action_at=r.next_action_at,
            ))
        s.commit()
        return len(records)


def load_records(leak_type: LeakType | None = None,
                 state: RecordState | None = RecordState.AT_RISK) -> list[AtRiskRecord]:
    stmt = select(AtRiskRecordRow)
    if leak_type is not None:
        stmt = stmt.where(AtRiskRecordRow.leak_type == leak_type.value)
    if state is not None:
        stmt = stmt.where(AtRiskRecordRow.state == state.value)
    with SessionLocal() as s:
        return [_to_model(r) for r in s.scalars(stmt.order_by(AtRiskRecordRow.id))]


def get_customer(customer_id: str) -> CustomerRow | None:
    with SessionLocal() as s:
        return s.get(CustomerRow, customer_id)


def count_records() -> int:
    with SessionLocal() as s:
        return s.query(AtRiskRecordRow).count()


def contact_history(before, window_days: int = 7):
    """Contacts per customer inside the trailing window, from what was actually
    executed — not from a per-batch tally.

    Guardrail #7 claims at most two contacts per customer per seven days. A
    counter that resets when the process restarts makes that claim true only
    within one run, which is the same as not being true.
    """
    from datetime import timedelta

    from .db import InterventionRow

    since = before - timedelta(days=window_days)
    counts: dict[str, int] = {}
    last: dict[str, object] = {}

    with SessionLocal() as s:
        rows = (
            s.query(InterventionRow.executed_at, AtRiskRecordRow.counterparty_id)
            .join(AtRiskRecordRow, AtRiskRecordRow.id == InterventionRow.record_id)
            .filter(InterventionRow.channel.isnot(None))
            .filter(InterventionRow.outcome == "EXECUTED")
            .filter(InterventionRow.executed_at.isnot(None))
            .all()
        )

    for executed_at, customer_id in rows:
        if executed_at is None:
            continue
        executed_at = to_ist(executed_at)
        if executed_at < since:
            continue
        counts[customer_id] = counts.get(customer_id, 0) + 1
        if customer_id not in last or executed_at > last[customer_id]:
            last[customer_id] = executed_at

    return counts, last
