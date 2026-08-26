"""Outcome attribution — the chain that proves the recovery was ours.

    payment_link.paid
        -> the link id we minted
        -> the intervention that minted it
        -> the record that intervention was chasing
        -> ₹ attributed to that record, and only that record

Without this walk you are polling Razorpay, seeing money arrive, and assuming
you caused it. The merchant's customers pay for all sorts of reasons; a number
that counts every payment as a recovery is a number a judge is right to
disbelieve. Attribution is what makes "₹2.03L recovered" a claim rather than a
coincidence.

Three properties hold here:

  * **Idempotent.** Razorpay retries webhooks. UNIQUE(event_id) claims the
    delivery; a second copy is recognised and dropped.
  * **Single-attribution.** Two different events can describe one payment
    (`payment.captured` and `payment_link.paid` arrive together). An
    intervention that already carries a result is never credited twice.
  * **Loud when it fails.** An event that matches no intervention is logged as
    UNATTRIBUTED, not silently discarded. Money we cannot explain is not money
    we get to count.
"""

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import desc
from sqlalchemy.exc import IntegrityError

from .. import audit
from ..db import (
    AtRiskRecordRow,
    InterventionRow,
    SessionLocal,
    WebhookEventRow,
    init_db,
)
from ..enums import RecordState, Stage
from ..clock import now
from .events import MalformedEvent, WebhookEvent, parse

log = logging.getLogger(__name__)

# Outcomes of handling one delivery. All of them are 200 responses to Razorpay —
# a 500 just means it retries a webhook we already understood.
PROCESSED = "PROCESSED"
DUPLICATE = "DUPLICATE"
ALREADY_ATTRIBUTED = "ALREADY_ATTRIBUTED"
UNATTRIBUTED = "UNATTRIBUTED"
IGNORED = "IGNORED"
MALFORMED = "MALFORMED"

RESULT_RECOVERED = "RECOVERED"
RESULT_FAILED_AGAIN = "FAILED_AGAIN"
RESULT_NO_RESPONSE = "NO_RESPONSE"

# Audit rows for events that belong to no record still need a record_id. A
# sentinel keeps them queryable instead of dropping them on the floor.
ORPHAN = "UNATTRIBUTED"


@dataclass
class Attribution:
    outcome: str
    event_id: str
    event_type: str = ""
    record_id: str | None = None
    intervention_id: int | None = None
    amount: int = 0
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "record_id": self.record_id,
            "intervention_id": self.intervention_id,
            "amount_paise": self.amount,
            "reason": self.reason,
        }


def _claim(event: WebhookEvent, *, simulated: bool) -> bool:
    """Reserve this delivery. False means it has been seen before."""
    row = WebhookEventRow(
        event_id=event.event_id,
        event_type=event.event_type,
        razorpay_ref=event.refs[0] if event.refs else None,
        amount=event.amount,
        outcome="RECEIVED",
        simulated=simulated,
        payload=event.raw,
        received_at=now(),
    )
    try:
        with SessionLocal() as session:
            session.add(row)
            session.commit()
        return True
    except IntegrityError:
        return False


def _settle_event_row(event_id: str, outcome: str, record_id: str | None) -> None:
    with SessionLocal() as session:
        row = (session.query(WebhookEventRow)
               .filter_by(event_id=event_id).one_or_none())
        if row is not None:
            row.outcome = outcome
            row.record_id = record_id
            session.commit()


def _find_intervention(session, event: WebhookEvent) -> InterventionRow | None:
    """Most specific match first: the exact Razorpay id we minted, then the
    record id we stamped into the payment's notes."""
    for ref in event.refs:
        row = (session.query(InterventionRow)
               .filter(InterventionRow.razorpay_ref == ref)
               .order_by(desc(InterventionRow.id))
               .first())
        if row is not None:
            return row

    if event.record_id:
        return (session.query(InterventionRow)
                .filter(InterventionRow.record_id == event.record_id)
                .filter(InterventionRow.outcome == "EXECUTED")
                .order_by(desc(InterventionRow.id))
                .first())
    return None


def handle(
    body: dict[str, Any],
    *,
    event_id: str | None = None,
    simulated: bool = False,
) -> Attribution:
    """Handle one verified webhook delivery. The caller has already checked the
    signature; this function assumes the body is authentic and nothing else."""
    init_db()

    try:
        event = parse(body, event_id=event_id)
    except MalformedEvent as exc:
        log.warning("malformed webhook: %s", exc)
        return Attribution(outcome=MALFORMED, event_id=event_id or "", reason=str(exc))

    if not _claim(event, simulated=simulated):
        return Attribution(outcome=DUPLICATE, event_id=event.event_id,
                           event_type=event.event_type,
                           reason="Delivery already handled; webhooks retry.")

    if not event.handled:
        _settle_event_row(event.event_id, IGNORED, None)
        return Attribution(outcome=IGNORED, event_id=event.event_id,
                           event_type=event.event_type,
                           reason=f"No handler for {event.event_type}.")

    with SessionLocal() as session:
        intervention = _find_intervention(session, event)

        if intervention is None:
            reason = (f"{event.event_type} matched no intervention "
                      f"(refs={list(event.refs) or 'none'}).")
            audit.log(ORPHAN, Stage.OUTCOME, UNATTRIBUTED, reason,
                      payload={"event_id": event.event_id,
                               "event_type": event.event_type,
                               "amount_paise": event.amount,
                               "simulated": simulated})
            _settle_event_row(event.event_id, UNATTRIBUTED, None)
            return Attribution(outcome=UNATTRIBUTED, event_id=event.event_id,
                               event_type=event.event_type, amount=event.amount,
                               reason=reason)

        record = session.get(AtRiskRecordRow, intervention.record_id)

        # Only money already counted is untouchable. NO_RESPONSE and
        # FAILED_AGAIN both mean "no payment yet", not "no payment ever" — a
        # customer can open a link days after it was sent, and a failed attempt
        # can be retried on the same link and succeed. Treating either as final
        # silently discards real money and leaves the agent chasing a record
        # that has already paid.
        if intervention.result == RESULT_RECOVERED:
            reason = (f"Intervention {intervention.id} already recovered "
                      f"{intervention.recovered_amount} paise; not counted twice.")
            audit.log(intervention.record_id, Stage.OUTCOME, ALREADY_ATTRIBUTED,
                      reason, payload={"event_id": event.event_id,
                                       "event_type": event.event_type,
                                       "simulated": simulated})
            _settle_event_row(event.event_id, ALREADY_ATTRIBUTED,
                              intervention.record_id)
            return Attribution(outcome=ALREADY_ATTRIBUTED, event_id=event.event_id,
                               event_type=event.event_type,
                               record_id=intervention.record_id,
                               intervention_id=intervention.id, reason=reason)

        if event.succeeded:
            # Never credit more than the record was worth. An overpayment is a
            # merchant's problem, not a bigger recovery number.
            amount = min(event.amount, record.amount) if (
                event.amount and record) else (record.amount if record else event.amount)

            was = intervention.result
            intervention.result = RESULT_RECOVERED
            intervention.recovered_amount = amount
            intervention.settled_at = now()
            if record is not None:
                record.state = RecordState.RECOVERED.value
                record.next_action_at = None
            session.commit()

            late = (f" Paid after this attempt was recorded as {was}."
                    if was else "")
            reason = (
                f"{event.event_type} on {event.refs[0] if event.refs else '?'} "
                f"traced to intervention {intervention.id} "
                f"({intervention.action_type}, attempt "
                f"{intervention.attempt_number}, policy "
                f"{intervention.policy_ref}). Record marked RECOVERED.{late}"
            )
            audit.log(intervention.record_id, Stage.OUTCOME, RESULT_RECOVERED,
                      reason,
                      payload={"event_id": event.event_id,
                               "event_type": event.event_type,
                               "razorpay_ref": intervention.razorpay_ref,
                               "intervention_id": intervention.id,
                               "recovered_paise": amount,
                               "attempt_number": intervention.attempt_number,
                               "policy_ref": intervention.policy_ref,
                               "upgraded_from": was,
                               "simulated": simulated})
            _settle_event_row(event.event_id, PROCESSED, intervention.record_id)
            return Attribution(outcome=PROCESSED, event_id=event.event_id,
                               event_type=event.event_type,
                               record_id=intervention.record_id,
                               intervention_id=intervention.id, amount=amount,
                               reason=reason)

        # payment.failed — the attempt is spent, the record is not recovered.
        # Bumping attempts here is what makes the max-attempts guardrail count
        # reality rather than intentions, which is also why it must only happen
        # the first time this attempt resolves: a second failure event on an
        # attempt already marked NO_RESPONSE would spend the budget twice.
        first_resolution = intervention.result is None
        intervention.result = RESULT_FAILED_AGAIN
        intervention.settled_at = now()
        if record is not None and first_resolution:
            record.attempts = (record.attempts or 0) + 1
        session.commit()

        reason = (f"{event.event_type} on {event.refs[0] if event.refs else '?'}: "
                  f"attempt {intervention.attempt_number} did not recover. "
                  f"{(event.error or {}).get('description') or ''}".strip())
        audit.log(intervention.record_id, Stage.OUTCOME, RESULT_FAILED_AGAIN,
                  reason,
                  payload={"event_id": event.event_id,
                           "event_type": event.event_type,
                           "error": event.error,
                           "intervention_id": intervention.id,
                           "simulated": simulated})
        _settle_event_row(event.event_id, PROCESSED, intervention.record_id)
        return Attribution(outcome=PROCESSED, event_id=event.event_id,
                           event_type=event.event_type,
                           record_id=intervention.record_id,
                           intervention_id=intervention.id, reason=reason)


def mark_no_response(intervention_id: int, *, reason: str = "") -> None:
    """The customer did nothing. Recorded explicitly so an unrecovered record is
    distinguishable from one still waiting on an answer."""
    with SessionLocal() as session:
        row = session.get(InterventionRow, intervention_id)
        if row is None or row.result is not None:
            return
        row.result = RESULT_NO_RESPONSE
        row.settled_at = now()
        # The attempt is spent whether or not anyone answered. Without this the
        # record proposes attempt 1 forever, its idempotency key is already
        # claimed, and it is blocked as a replay for the rest of its life
        # instead of moving to step 2 of the policy schedule.
        record = session.get(AtRiskRecordRow, row.record_id)
        if record is not None:
            record.attempts = (record.attempts or 0) + 1
        session.commit()
        record_id = row.record_id
    audit.log(record_id, Stage.OUTCOME, RESULT_NO_RESPONSE,
              reason or "Intervention delivered; no payment followed.",
              payload={"intervention_id": intervention_id})
