"""Customers who were always going to pay.

Without this the simulator asserts that a record nobody touches never recovers,
which is false and which flatters the agent twice over: every record it
correctly declines to chase reads as a total loss, and any comparison against a
strategy that acts less wins by construction rather than by working.

The tests that matter most here are the ones about *not* claiming it. An
unprompted payment that gets attributed to an intervention is a scoreboard
counting somebody else's work as its own, and that is a harder failure to notice
than a crash.
"""

import pytest

from reclaim import clock
from reclaim.db import (
    AtRiskRecordRow, HumanQueueRow, InterventionRow, SessionLocal,
    reset_database,
)
from reclaim.enums import RecordState, RootCause
from reclaim.synthetic import generate, razorpay_payloads as payloads
from reclaim.synthetic.outcomes import SELF_CURE
from reclaim.webhooks.attribution import ORGANIC, PROCESSED, handle


@pytest.fixture(autouse=True)
def _clean_db():
    reset_database()
    clock.reset()


# --- the draw --------------------------------------------------------------


def test_the_draw_is_reproducible_from_the_seed():
    assert generate(seed=42).self_cure.keys() == generate(seed=42).self_cure.keys()


def test_a_different_seed_draws_different_customers():
    assert generate(seed=42).self_cure.keys() != generate(seed=43).self_cure.keys()


def test_nobody_who_cannot_pay_pays_anyway():
    """A card the issuer has blocked will be blocked again; a revoked mandate has
    nothing left to debit. Letting either self-cure would manufacture money."""
    batch = generate(seed=42)
    impossible = {c for c, rate in SELF_CURE.items() if rate == 0.0}
    offenders = [rid for rid in batch.self_cure
                 if batch.truth[rid] in impossible]
    assert offenders == []


def test_the_draw_does_not_move_the_batch():
    """Self-cure is drawn from its own stream, after every other draw. If it
    ever shared one, tuning a rate here would silently reshuffle every record
    and every published number with it."""
    batch = generate(seed=42)
    assert len(batch.records) == 180
    assert batch.total_at_risk == 1120981400


def test_self_cure_is_keyed_on_truth_not_on_diagnosis():
    """The property that makes any comparison using it sound. Whether somebody
    pays is a fact about them; what we decided their error code meant cannot
    change it, so both arms are handed the same customers."""
    batch = generate(seed=42)
    for rid in batch.self_cure:
        assert SELF_CURE[batch.truth[rid]] > 0


# --- not claiming it -------------------------------------------------------


def _record(rid="REC_1", *, amount=50_000_00, source_ref="order_original"):
    with SessionLocal() as session:
        session.add(AtRiskRecordRow(
            id=rid, leak_type="FAILED_PAYMENT", amount=amount, currency="INR",
            counterparty_id="CUST_1", source_ref=source_ref,
            detected_at=clock.now(), due_at=None, raw_signals={},
            state=RecordState.AT_RISK.value, attempts=0, next_action_at=None))
        session.commit()
    return rid


def _executed_intervention(rid: str, ref="plink_ours"):
    with SessionLocal() as session:
        row = InterventionRow(
            record_id=rid, action_type="SEND_LINK", channel="EMAIL",
            policy_ref="FAILED_PAYMENT.INSUFFICIENT_FUNDS", attempt_number=1,
            scheduled_for=clock.now(), executed_at=clock.now(),
            razorpay_ref=ref, outcome="EXECUTED")
        session.add(row)
        session.commit()


def test_an_unprompted_payment_is_not_credited_to_an_intervention():
    """THE bug this file exists for.

    Attribution falls back to matching on the record id in a payment's notes,
    which is correct — our executor writes that note on everything it mints, so
    a note is proof the money came through us. An unprompted payment carries no
    such note, and must not be adopted by an intervention that happens to name
    the same record.
    """
    rid = _record()
    _executed_intervention(rid)

    result = handle(payloads.payment_captured_unprompted(
        payment_id="pay_organic", amount=50_000_00, order_id="order_original"))

    assert result.outcome == ORGANIC
    assert result.amount == 0, "the agent was credited for a payment it did not cause"

    with SessionLocal() as session:
        assert session.get(AtRiskRecordRow, rid).state == (
            RecordState.RECOVERED.value)
        row = session.query(InterventionRow).one()
        assert row.result is None
        assert row.recovered_amount in (0, None)


def test_a_payment_carrying_our_note_is_still_ours():
    """The other side of the same rule. Only we write that note, so a payment
    that carries it came through something we made — even on a payment id we
    have never seen."""
    rid = _record()
    _executed_intervention(rid)

    result = handle(payloads.payment_captured(
        payment_id="pay_unknown", amount=50_000_00, record_id=rid))

    assert result.outcome == PROCESSED
    assert result.amount == 50_000_00


def test_an_unprompted_payment_for_nobody_is_unattributed_not_invented():
    result = handle(payloads.payment_captured_unprompted(
        payment_id="pay_nobody", amount=1000, order_id="order_we_never_saw"))

    assert result.outcome == "UNATTRIBUTED"
    assert result.record_id is None


def test_a_record_already_recovered_is_not_recovered_twice():
    rid = _record()
    body = payloads.payment_captured_unprompted(
        payment_id="pay_organic", amount=50_000_00, order_id="order_original")

    first = handle(body, event_id="evt_1")
    second = handle(body, event_id="evt_2")

    assert first.outcome == ORGANIC
    assert second.outcome == "UNATTRIBUTED", (
        "a settled record accepted a second unprompted payment")


def test_an_unprompted_payment_takes_the_record_off_the_human_queue():
    rid = _record()
    with SessionLocal() as session:
        session.add(HumanQueueRow(record_id=rid, reason="escalated",
                                  amount=50_000_00, raised_at=clock.now()))
        session.commit()

    handle(payloads.payment_captured_unprompted(
        payment_id="pay_organic", amount=50_000_00, order_id="order_original"))

    with SessionLocal() as session:
        assert session.query(HumanQueueRow).one().resolved_at is not None


# --- the scoreboard --------------------------------------------------------


def test_organic_money_is_its_own_bucket_and_the_board_still_balances():
    """Neither recovery nor write-off. The money arrived, so it is not lost; the
    agent did not cause it, so it is not ours."""
    from reclaim.scoreboard import compute

    rid = _record()
    handle(payloads.payment_captured_unprompted(
        payment_id="pay_organic", amount=50_000_00, order_id="order_original"))

    board = compute()
    assert board.organic_paise == 50_000_00
    assert board.organic_records == 1
    assert board.recovered_paise == 0, "an unprompted payment inflated recovery"
    assert board.unrecoverable_paise == 0, "money that arrived was written off"
    assert board.balances
