"""One record, start to finish, on one screen.

The brief's worked-example request, verbatim: "demonstrate one full case from
initial drop/failure detection to confirmed money recovery." The dashboard
shows this as a clickable trail; this is the same walk as a command, so it
can be run on any record, pasted into a README, or read by a judge who never
opened the UI.

Everything here is read from storage - the audit log, the interventions, the
webhook events, the queue, the promises. Nothing is recomputed, because the
point is to show what the agent recorded at the moment it decided, not what it
would decide now.

The one column worth reading twice is `simulated` on each webhook event. It is
the row-level answer to "is this money real": False means Razorpay sent it,
True means the outcome simulator did. The scoreboard adds the two up; this
keeps them apart.
"""

from typing import Any

from . import audit
from .db import (
    AtRiskRecordRow, CustomerRow, HumanQueueRow, InterventionRow, PromiseRow,
    SessionLocal, WebhookEventRow,
)
from .money import format_inr


def trace(record_id: str) -> dict[str, Any] | None:
    with SessionLocal() as session:
        record = session.get(AtRiskRecordRow, record_id)
        if record is None:
            return None
        customer = session.get(CustomerRow, record.counterparty_id)
        interventions = (session.query(InterventionRow)
                         .filter(InterventionRow.record_id == record_id)
                         .order_by(InterventionRow.id).all())
        events = (session.query(WebhookEventRow)
                  .filter(WebhookEventRow.record_id == record_id)
                  .order_by(WebhookEventRow.id).all())
        queue = (session.query(HumanQueueRow)
                 .filter(HumanQueueRow.record_id == record_id)
                 .order_by(HumanQueueRow.id).all())
        promises = (session.query(PromiseRow)
                    .filter(PromiseRow.record_id == record_id)
                    .order_by(PromiseRow.id).all())

        signals = record.raw_signals or {}
        error = signals.get("error") or {}
        recovered = sum(i.recovered_amount or 0 for i in interventions
                        if i.result == "RECOVERED")
        real = [e for e in events if not e.simulated]

        return {
            "record": {
                "id": record.id,
                "leak_type": record.leak_type,
                "amount_paise": record.amount,
                "amount": format_inr(record.amount),
                "state": record.state,
                "attempts": record.attempts,
                "detected_at": _iso(record.detected_at),
                "source_ref": record.source_ref,
                "counterparty_id": record.counterparty_id,
                "issuer_bank": signals.get("issuer_bank"),
                "method": signals.get("method"),
                "error_reason": error.get("reason"),
                "error_description": error.get("description"),
            },
            "customer": None if customer is None else {
                "id": customer.id,
                "opted_out": bool(customer.opted_out),
                "on_dnd": bool(customer.on_dnd),
            },
            "trail": [{
                "at": _iso(r.at),
                "stage": r.stage,
                "outcome": r.outcome,
                "guardrail": r.guardrail,
                "reason": r.reason,
                "deferred_until": _iso(r.deferred_until),
                "payload": r.payload or {},
            } for r in audit.timeline(record_id)],
            "interventions": [{
                "id": i.id,
                "attempt": i.attempt_number,
                "action": i.action_type,
                "channel": i.channel,
                "policy_ref": i.policy_ref,
                "scheduled_for": _iso(i.scheduled_for),
                "executed_at": _iso(i.executed_at),
                "razorpay_ref": i.razorpay_ref,
                "outcome": i.outcome,
                "result": i.result,
                "recovered_amount": i.recovered_amount or 0,
            } for i in interventions],
            "webhooks": [{
                "event_id": e.event_id,
                "event_type": e.event_type,
                "razorpay_ref": e.razorpay_ref,
                "amount_paise": e.amount,
                "outcome": e.outcome,
                "simulated": bool(e.simulated),
            } for e in events],
            "human_queue": [{
                "raised_at": _iso(q.raised_at),
                "resolved_at": _iso(q.resolved_at),
                "reason": q.reason,
            } for q in queue],
            "promises": [{
                "state": p.state,
                "promised_at": _iso(p.promised_at),
                "promised_for": _iso(p.promised_for),
                "reply_text": p.reply_text,
            } for p in promises],
            "money": {
                "at_risk_paise": record.amount,
                "recovered_paise": recovered,
                "recovered": format_inr(recovered),
                "confirmed_by_razorpay": bool(real),
                "real_events": len(real),
                "modelled_events": len(events) - len(real),
            },
        }


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None
