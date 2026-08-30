"""The human queue: closing what is done, ordering what is not.

Both behaviours here are about a person's attention. The closing tests exist
because the column was there from Day 1 and nothing ever wrote it, so a record
that paid on Friday stayed on somebody's list for ever. The ordering tests exist
because sorting by amount put the one row where the correct action is *none* at
the top of the list.
"""

from datetime import timedelta

import pytest

from reclaim import clock, human_queue
from reclaim.db import (
    AtRiskRecordRow, HumanQueueRow, SessionLocal, reset_database,
)
from reclaim.enums import LeakType, RecordState, RootCause
from reclaim.human_queue import Tier


@pytest.fixture(autouse=True)
def _clean_db():
    reset_database()
    clock.reset()


def _record(rid: str, *, amount: int, leak: LeakType = LeakType.FAILED_PAYMENT,
            state: RecordState = RecordState.ESCALATED, attempts: int = 0):
    with SessionLocal() as session:
        session.add(AtRiskRecordRow(
            id=rid, leak_type=leak.value, amount=amount, currency="INR",
            counterparty_id=f"CUST_{rid}", source_ref=f"pay_{rid}",
            detected_at=clock.now(), due_at=None, raw_signals={},
            state=state.value, attempts=attempts, next_action_at=None,
        ))
        session.commit()


def _queue(rid: str, *, amount: int, reason: str = "escalated", ago_days=0.0):
    with SessionLocal() as session:
        session.add(HumanQueueRow(
            record_id=rid, reason=reason, amount=amount,
            raised_at=clock.now() - timedelta(days=ago_days),
        ))
        session.commit()


# --- closing ---------------------------------------------------------------


def test_resolving_closes_every_open_row_for_the_record():
    _record("R1", amount=10_000_00)
    _queue("R1", amount=10_000_00)

    assert human_queue.resolve("R1") == 1

    with SessionLocal() as session:
        row = session.query(HumanQueueRow).one()
        assert row.resolved_at is not None


def test_resolving_is_idempotent():
    """A record can reach a terminal state more than once — a webhook retries,
    a stopping rule fires on a record already recovered. Closing twice must not
    re-stamp a row or report work it did not do."""
    _record("R1", amount=10_000_00)
    _queue("R1", amount=10_000_00)

    assert human_queue.resolve("R1") == 1
    assert human_queue.resolve("R1") == 0


def test_resolving_leaves_the_escalation_reason_alone():
    """Why a row CLOSED is derivable from the record's final state. Why it was
    RAISED is not derivable from anything, so overwriting it destroys the more
    useful of the two."""
    _record("R1", amount=10_000_00)
    _queue("R1", amount=10_000_00, reason="above the auto-action ceiling")

    human_queue.resolve("R1")

    with SessionLocal() as session:
        assert session.query(HumanQueueRow).one().reason == (
            "above the auto-action ceiling")


def test_resolved_rows_are_not_open_work():
    _record("R1", amount=10_000_00)
    _record("R2", amount=20_000_00)
    _queue("R1", amount=10_000_00)
    _queue("R2", amount=20_000_00)

    human_queue.resolve("R1")

    ids = [i["record_id"] for i in human_queue.open_items(causes={})]
    assert ids == ["R2"]
    assert human_queue.resolved_count() == 1


# --- tiering ---------------------------------------------------------------


def test_a_cause_that_must_never_be_chased_is_not_work():
    """A revoked mandate for ₹2 lakh is not a large decision awaiting a
    signature. It is not a decision at all, and it must not outrank one."""
    tier = human_queue.tier_for(
        RootCause.MANDATE_REVOKED.value,
        leak_type=LeakType.FAILED_MANDATE.value, amount=200_000_00)
    assert tier is Tier.FOR_THE_RECORD


def test_above_the_ceiling_blocks_the_agent():
    tier = human_queue.tier_for(
        RootCause.INSUFFICIENT_FUNDS.value,
        leak_type=LeakType.FAILED_PAYMENT.value, amount=68_000_00)
    assert tier is Tier.BLOCKING


def test_everything_else_needs_judgement():
    tier = human_queue.tier_for(
        RootCause.UNKNOWN.value,
        leak_type=LeakType.FAILED_PAYMENT.value, amount=12_000_00)
    assert tier is Tier.JUDGEMENT


# --- expected value --------------------------------------------------------


def test_a_dead_mandate_is_worth_nothing_however_large():
    ev, _ = human_queue.expected_value(
        amount=80_000_00, root_cause=RootCause.MANDATE_REVOKED.value,
        leak_type=LeakType.FAILED_MANDATE.value, attempts=0, age_days=0)
    assert ev == 0


def test_a_smaller_recoverable_record_outranks_a_larger_dead_one():
    """The whole point of ranking by value rather than amount."""
    dead, _ = human_queue.expected_value(
        amount=80_000_00, root_cause=RootCause.MANDATE_REVOKED.value,
        leak_type=LeakType.FAILED_MANDATE.value, attempts=0, age_days=0)
    live, _ = human_queue.expected_value(
        amount=40_000_00, root_cause=RootCause.EXPIRED_INSTRUMENT.value,
        leak_type=LeakType.FAILED_PAYMENT.value, attempts=0, age_days=0)
    assert live > dead


def test_an_unknown_cause_is_scored_but_flagged_as_an_estimate():
    """No cause means no prior. Scoring it on the mean of what it could turn out
    to be is defensible; printing that as a confident number is not."""
    ev, estimated = human_queue.expected_value(
        amount=40_000_00, root_cause=RootCause.UNKNOWN.value,
        leak_type=LeakType.FAILED_PAYMENT.value, attempts=0, age_days=0)
    assert ev > 0
    assert estimated is True

    _, known = human_queue.expected_value(
        amount=40_000_00, root_cause=RootCause.EXPIRED_INSTRUMENT.value,
        leak_type=LeakType.FAILED_PAYMENT.value, attempts=0, age_days=0)
    assert known is False


def test_value_decays_with_waiting_and_carts_decay_fastest():
    cart_fresh, _ = human_queue.expected_value(
        amount=10_000_00, root_cause=RootCause.CART_ABANDONMENT.value,
        leak_type=LeakType.ABANDONED_CART.value, attempts=0, age_days=0)
    cart_old, _ = human_queue.expected_value(
        amount=10_000_00, root_cause=RootCause.CART_ABANDONMENT.value,
        leak_type=LeakType.ABANDONED_CART.value, attempts=0, age_days=3)
    assert cart_old < cart_fresh

    # An invoice is worth roughly the same in three days; a cart is not.
    inv_fresh, _ = human_queue.expected_value(
        amount=10_000_00, root_cause=RootCause.AWAITING_APPROVAL.value,
        leak_type=LeakType.OVERDUE_INVOICE.value, attempts=0, age_days=0)
    inv_old, _ = human_queue.expected_value(
        amount=10_000_00, root_cause=RootCause.AWAITING_APPROVAL.value,
        leak_type=LeakType.OVERDUE_INVOICE.value, attempts=0, age_days=3)
    assert inv_old / inv_fresh > cart_old / cart_fresh


# --- ordering --------------------------------------------------------------


def test_the_queue_puts_a_signature_above_a_bigger_dead_record():
    """The regression this module exists for: sorted by amount, the mandate was
    first and the payment waiting on a human was second."""
    _record("DEAD", amount=80_000_00, leak=LeakType.FAILED_MANDATE)
    _record("BLOCKED", amount=68_000_00, leak=LeakType.FAILED_PAYMENT)
    _queue("DEAD", amount=80_000_00)
    _queue("BLOCKED", amount=68_000_00)

    items = human_queue.open_items(causes={
        "DEAD": RootCause.MANDATE_REVOKED.value,
        "BLOCKED": RootCause.INSUFFICIENT_FUNDS.value,
    })

    assert [i["record_id"] for i in items] == ["BLOCKED", "DEAD"]
    assert items[0]["tier"] == int(Tier.BLOCKING)
    assert items[1]["tier"] == int(Tier.FOR_THE_RECORD)


def test_open_work_excludes_the_for_the_record_lane():
    _record("DEAD", amount=80_000_00, leak=LeakType.FAILED_MANDATE)
    _record("WORK", amount=12_000_00)
    _queue("DEAD", amount=80_000_00)
    _queue("WORK", amount=12_000_00)

    items = human_queue.open_items(causes={
        "DEAD": RootCause.MANDATE_REVOKED.value,
        "WORK": RootCause.INSUFFICIENT_FUNDS.value,
    })
    work = [i for i in items if i["tier"] < int(Tier.FOR_THE_RECORD)]

    assert len(items) == 2
    assert [i["record_id"] for i in work] == ["WORK"]


# --- the whole scenario, through the real attribution path -----------------


def test_a_record_escalated_then_paid_leaves_the_queue():
    """The case that motivated all of this, end to end.

    A record is escalated on Monday and the customer pays on Friday. Guardrail
    11 already stops the AGENT chasing it; this asserts nobody sends a PERSON
    either. Driven through the real webhook -> attribution path rather than by
    setting the state directly, because the bug was that nothing on that path
    ever closed the row.
    """
    from reclaim.db import InterventionRow
    from reclaim.runner import run_batch
    from reclaim.synthetic import razorpay_payloads as payloads
    from reclaim.webhooks.attribution import PROCESSED, handle

    run_batch(dry_run=True, settle=False)
    with SessionLocal() as session:
        intervention = (session.query(InterventionRow)
                        .filter(InterventionRow.razorpay_ref.isnot(None))
                        .order_by(InterventionRow.id).first())
        assert intervention is not None
        record_id = intervention.record_id
        ref = intervention.razorpay_ref
        amount = session.get(AtRiskRecordRow, record_id).amount

    # Escalate it, exactly as the runner would on a value-ceiling refusal. The
    # batch has escalated records of its own, so this asserts about ours rather
    # than about the length of the queue.
    _queue(record_id, amount=amount, reason="above the auto-action ceiling")
    assert record_id in {i["record_id"]
                         for i in human_queue.open_items(causes={})}

    result = handle(payloads.payment_link_paid(
        link_id=ref, payment_id="pay_late", amount=amount, record_id=record_id))
    assert result.outcome == PROCESSED

    with SessionLocal() as session:
        assert session.get(AtRiskRecordRow, record_id).state == (
            RecordState.RECOVERED.value)
        row = session.query(HumanQueueRow).filter(
            HumanQueueRow.record_id == record_id).one()
        assert row.resolved_at is not None, (
            "the money arrived and the row is still on somebody's list")

    assert record_id not in {i["record_id"]
                             for i in human_queue.open_items(causes={})}
    assert human_queue.resolved_count() >= 1


def test_a_reply_arriving_after_the_money_does_not_queue_anyone():
    """A reply can land after the invoice was already paid.

    The contact goes out, the customer pays, and their answer to the contact
    arrives afterwards — settlement runs before replies are read, so this is the
    normal order, not a race. Reading the reply is still right; putting a
    settled record on somebody's desk is not.
    """
    from reclaim.brain.conversation.handler import _to_human
    from reclaim.repository import load_records

    _record("R1", amount=10_000_00, state=RecordState.RECOVERED)
    record = next(r for r in load_records(state=None) if r.id == "R1")

    _to_human(record, "reply not confidently understood", clock.now())

    with SessionLocal() as session:
        assert session.query(HumanQueueRow).count() == 0, (
            "a record that already paid was put back on somebody's list")
