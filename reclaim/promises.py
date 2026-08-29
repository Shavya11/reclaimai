"""Promise-to-pay: the record of what somebody committed to, and its fate.

Kept out of `repository.py` because a promise is not a record and not an
intervention — it is a third thing with its own small state machine, and the two
transitions it can make (KEPT, BROKEN) are the entire product feature.

The state machine is deliberately dumb and deterministic. The model's only
involvement anywhere near this file is having labelled a sentence
PROMISE_TO_PAY; the date, the amount, the record it attaches to and what happens
when it lapses are all decided here.
"""

from datetime import datetime, timedelta

from .clock import now
from .db import AtRiskRecordRow, PromiseRow, SessionLocal
from .enums import PromiseState, RecordState
from .timeutil import to_ist

# A promise further out than this is not a promise, it is a brush-off, and
# honouring it would park a record for a quarter. Read through rules.py so a
# merchant can argue with the number.
DEFAULT_MAX_HORIZON_DAYS = 45


def max_horizon_days() -> int:
    from .brain import rules

    return int(rules.threshold("promise_window", "max_horizon_days",
                               default=DEFAULT_MAX_HORIZON_DAYS))


def validate_date(promised_for: datetime | None, *, frm: datetime | None = None
                  ) -> tuple[datetime | None, str]:
    """Deterministic gate on a date the model extracted.

    The model may read a date out of a sentence. It may not be trusted with what
    that date means. A date in the past, a date next year, or an unparseable one
    is not a promise the system will act on — it is an UNCLEAR that reaches a
    person. This is the same discipline as the rule about money, applied to
    time: the model labels, the deterministic code decides.
    """
    if promised_for is None:
        return None, "no date given"
    frm = frm or now()
    try:
        when = to_ist(promised_for)
    except (ValueError, TypeError):
        return None, "unparseable date"

    if when <= frm:
        return None, f"date {when:%Y-%m-%d} is not in the future"
    horizon = max_horizon_days()
    if when > frm + timedelta(days=horizon):
        return None, f"date {when:%Y-%m-%d} is beyond the {horizon}-day horizon"
    return when, ""


def record_promise(record_id: str, *, promised_for: datetime, amount: int,
                   intent: str, confidence: float, reply_text: str,
                   at: datetime | None = None) -> PromiseRow:
    """Open a promise and put the record to sleep until its date.

    One open promise per record. A buyer who promises twice has moved their
    date, not made a second commitment, so the earlier one is superseded rather
    than left to fire a spurious breach.
    """
    at = at or now()
    with SessionLocal() as session:
        for existing in (session.query(PromiseRow)
                         .filter(PromiseRow.record_id == record_id)
                         .filter(PromiseRow.state == PromiseState.OPEN.value)):
            existing.state = PromiseState.BROKEN.value
            existing.resolved_at = at

        row = PromiseRow(
            record_id=record_id, promised_at=at, promised_for=promised_for,
            amount=amount, source_intent=intent, confidence=confidence,
            reply_text=reply_text[:2000], state=PromiseState.OPEN.value,
        )
        session.add(row)

        record = session.get(AtRiskRecordRow, record_id)
        if record is not None and not RecordState(record.state).is_terminal:
            record.state = RecordState.PROMISED.value
            record.next_action_at = promised_for
        session.commit()
        session.refresh(row)
        return row


def open_promises(at: datetime | None = None) -> dict[str, datetime]:
    """record_id -> promised_for, for promises still standing. Read by guardrail
    #14 on every action, so it is one query per batch, not one per record."""
    with SessionLocal() as session:
        return {r.record_id: to_ist(r.promised_for) for r in
                session.query(PromiseRow)
                .filter(PromiseRow.state == PromiseState.OPEN.value)}


def settle_due(at: datetime | None = None) -> tuple[list[str], list[str]]:
    """Resolve every promise whose date has passed. Returns (kept, broken).

    Kept is decided by the money, never by the promise: a record that reached
    RECOVERED by its date kept it, and nothing else did. Taking the customer's
    word for it is how a scoreboard starts counting sentences as rupees.
    """
    at = at or now()
    kept: list[str] = []
    broken: list[str] = []

    with SessionLocal() as session:
        due = (session.query(PromiseRow)
               .filter(PromiseRow.state == PromiseState.OPEN.value)
               .filter(PromiseRow.promised_for <= at)
               .order_by(PromiseRow.id).all())
        for promise in due:
            record = session.get(AtRiskRecordRow, promise.record_id)
            paid = record is not None and record.state == RecordState.RECOVERED.value
            promise.state = (PromiseState.KEPT if paid
                             else PromiseState.BROKEN).value
            promise.resolved_at = at
            (kept if paid else broken).append(promise.record_id)

            # A broken promise wakes the record up. It does not close it and it
            # does not escalate it on its own — it hands it back to the ladder,
            # one rung further along, which is what the extra attempt does.
            if not paid and record is not None and \
                    record.state == RecordState.PROMISED.value:
                record.state = RecordState.AT_RISK.value
                record.next_action_at = None
        session.commit()

    return kept, broken


def counts() -> dict[str, int]:
    with SessionLocal() as session:
        out = {s.value: 0 for s in PromiseState}
        for state, in session.query(PromiseRow.state):
            out[state] = out.get(state, 0) + 1
        return out


def for_record(record_id: str) -> list[dict]:
    with SessionLocal() as session:
        return [{
            "promised_at": to_ist(r.promised_at).isoformat(),
            "promised_for": to_ist(r.promised_for).isoformat(),
            "amount": r.amount,
            "state": r.state,
            "confidence": r.confidence,
            "reply_text": r.reply_text,
            "resolved_at": to_ist(r.resolved_at).isoformat() if r.resolved_at else None,
        } for r in session.query(PromiseRow)
          .filter(PromiseRow.record_id == record_id)
          .order_by(PromiseRow.id)]
