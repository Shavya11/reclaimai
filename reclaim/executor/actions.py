"""Executes a permitted action.

Two things happen in a strict order, and the order is the whole guarantee:

  1. Claim the idempotency key by INSERTing into executed_actions.
  2. Only then call Razorpay.

Claiming first means a crash between the two leaves a claimed key and no charge —
the safe failure. Calling first would risk a charge with no record of it, and on
resume we would charge again. Payments systems are allowed to under-deliver on a
retry; they are not allowed to double-charge.

The UNIQUE constraint on idempotency_key is what makes step 1 atomic. Two
processes racing the same action have exactly one winner.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from ..db import ExecutedActionRow, InterventionRow, SessionLocal
from ..enums import ActionType, Channel
from ..models import ProposedAction
from ..clock import now
from .channels import ChannelSender, Delivery, recipient_for
from .messages import render
from .razorpay_client import RazorpayClient, RazorpayError

log = logging.getLogger(__name__)


class AlreadyExecuted(Exception):
    """The idempotency key was already claimed. Not an error — a guarantee
    doing its job."""


@dataclass
class Execution:
    action: ProposedAction
    ok: bool
    razorpay_ref: str | None = None
    link_url: str | None = None
    delivery: Delivery | None = None
    error: str | None = None
    skipped: bool = False


def claim(action: ProposedAction, *, at: datetime | None = None) -> None:
    """Reserve the idempotency key. Raises AlreadyExecuted if someone got there
    first — including this same process on an earlier, crashed run."""
    row = ExecutedActionRow(
        idempotency_key=action.idempotency_key,
        record_id=action.record_id,
        attempt_number=action.attempt_number,
        action_type=action.action_type.value,
        executed_at=at or now(),
    )
    try:
        with SessionLocal() as session:
            session.add(row)
            session.commit()
    except IntegrityError as exc:
        raise AlreadyExecuted(action.idempotency_key) from exc


def execute(
    action: ProposedAction,
    *,
    customer=None,
    root_cause=None,
    tone: str = "gentle",
    prefill_method: str | None = None,
    client: RazorpayClient | None = None,
    sender: ChannelSender | None = None,
    merchant: str = "Acme Store",
    at: datetime | None = None,
) -> Execution:
    """Executes one permitted action. Never raises for an expected condition;
    a failed Razorpay call comes back as ok=False so the batch continues.

    `at` is the batch's own clock. It matters more than it looks: the schedule
    anchor for attempt N+1 and the seven-day window guardrail #7 counts over are
    both read back off `executed_at`, and both are computed relative to the time
    the batch believes it is running at. Stamping the wall clock instead leaves
    a batch running at 11:00 writing rows dated 20:14, so the follow-up it
    schedules and the window it counts disagree with the decision that produced
    them — by minutes usually, and by two months whenever the demo clock is
    advanced."""
    client = client or RazorpayClient()
    sender = sender or ChannelSender()

    try:
        claim(action, at=at)
    except AlreadyExecuted:
        log.info("skipping %s — already executed", action.idempotency_key)
        return Execution(action=action, ok=True, skipped=True)

    if action.action_type in {ActionType.ESCALATE, ActionType.NO_ACTION}:
        return _record(Execution(action=action, ok=True), at)

    try:
        if action.action_type is ActionType.SILENT_RETRY:
            order = client.create_order(
                action.amount, idempotency_key=action.idempotency_key,
                notes={"record_id": action.record_id, "reclaim": "silent_retry"},
            )
            return _record(Execution(action=action, ok=True,
                                     razorpay_ref=order.get("id")), at)

        link = client.create_payment_link(
            action.amount,
            idempotency_key=action.idempotency_key,
            prefill_method=prefill_method,
            description=f"Payment for {action.record_id}",
            notes={"record_id": action.record_id,
                   "attempt": str(action.attempt_number)},
        )
        url = link.get("short_url", "")
        execution = Execution(action=action, ok=True,
                              razorpay_ref=link.get("id"), link_url=url)

        if action.channel is not None and root_cause is not None:
            text = render(root_cause, amount=action.amount, link=url, tone=tone,
                          merchant=merchant)
            execution.delivery = sender.send(
                action.channel, recipient_for(action.channel, customer), text)

        return _record(execution, at)

    except RazorpayError as exc:
        # The key stays claimed. A retry of this exact attempt will be skipped
        # rather than risking a second charge; the record is parked for review.
        log.warning("execution failed for %s: %s", action.record_id, exc)
        return _record(Execution(action=action, ok=False, error=str(exc)), at)


def _record(execution: Execution, at: datetime | None = None) -> Execution:
    action = execution.action
    with SessionLocal() as session:
        session.add(InterventionRow(
            record_id=action.record_id,
            action_type=action.action_type.value,
            channel=action.channel.value if action.channel else None,
            policy_ref=action.policy_ref,
            attempt_number=action.attempt_number,
            scheduled_for=action.scheduled_for,
            executed_at=at or now(),
            razorpay_ref=execution.razorpay_ref,
            outcome="EXECUTED" if execution.ok else "FAILED",
        ))
        if execution.razorpay_ref:
            row = session.query(ExecutedActionRow).filter_by(
                idempotency_key=action.idempotency_key).one_or_none()
            if row is not None:
                row.razorpay_ref = execution.razorpay_ref
        session.commit()
    return execution


def executed_keys() -> set[str]:
    with SessionLocal() as session:
        return {k for (k,) in session.query(ExecutedActionRow.idempotency_key)}
