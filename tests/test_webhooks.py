"""Webhook receipt, signature verification and outcome attribution.

There is no public tunnel on this machine, so Razorpay cannot reach it and this
suite is the only thing standing behind the claim that the loop closes. It is
written to be the evidence, not a placeholder for it: every payload here is
Razorpay's real wire shape, every signature is a real HMAC, and the assertions
are about behaviour a live delivery would exhibit.

The first test in the file is the one that matters most. Verifying a
re-serialized body instead of the raw bytes is the classic webhook bug, it fails
silently in the direction of "signatures never match", and the usual fix is to
stop checking them.
"""

import json

import pytest

from reclaim import audit
from reclaim.db import (
    AtRiskRecordRow,
    InterventionRow,
    SessionLocal,
    WebhookEventRow,
    reset_database,
)
from reclaim.enums import RecordState, Stage
from reclaim.runner import run_batch
from reclaim.synthetic import razorpay_payloads as payloads
from reclaim.webhooks import parse, receive, sign, verify
from reclaim.webhooks.attribution import (
    ALREADY_ATTRIBUTED,
    DUPLICATE,
    IGNORED,
    ORPHAN,
    PROCESSED,
    RESULT_FAILED_AGAIN,
    RESULT_RECOVERED,
    UNATTRIBUTED,
    handle,
)

SECRET = "whsec_test_reclaim"


@pytest.fixture(autouse=True)
def _clean_db():
    reset_database()


def _raw(body: dict) -> bytes:
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


def _executed_intervention() -> InterventionRow:
    """One real intervention from a real batch, so attribution has something
    genuine to walk back to."""
    run_batch(dry_run=True, settle=False)
    with SessionLocal() as session:
        row = (session.query(InterventionRow)
               .filter(InterventionRow.razorpay_ref.isnot(None))
               .filter(InterventionRow.action_type == "SEND_LINK")
               .order_by(InterventionRow.id).first()
               or session.query(InterventionRow)
               .filter(InterventionRow.razorpay_ref.isnot(None))
               .order_by(InterventionRow.id).first())
        assert row is not None, "the batch produced no attributable intervention"
        session.expunge(row)
        return row


# --- signature ---------------------------------------------------------------


def test_signature_is_verified_against_raw_bytes_not_reserialized_json():
    """THE webhook bug. Razorpay signs the bytes it sent. Parsing and
    re-serialising produces different bytes — different key order, different
    separators — and the HMAC will not match."""
    body = payloads.payment_link_paid(
        link_id="plink_1", payment_id="pay_1", amount=50000, record_id="REC_1")
    raw = _raw(body)
    signature = sign(raw, SECRET)

    assert verify(raw, signature, SECRET)

    # What a re-serializing implementation would hand the verifier instead.
    reserialized = json.dumps(json.loads(raw), indent=2).encode("utf-8")
    assert reserialized != raw
    assert not verify(reserialized, signature, SECRET)


def test_a_tampered_body_fails_verification():
    body = payloads.payment_link_paid(
        link_id="plink_1", payment_id="pay_1", amount=50000, record_id="REC_1")
    raw = _raw(body)
    signature = sign(raw, SECRET)

    body["payload"]["payment_link"]["entity"]["amount_paid"] = 5_000_000
    assert not verify(_raw(body), signature, SECRET)


def test_wrong_secret_and_missing_signature_are_both_refused():
    raw = _raw({"event": "payment.captured"})
    assert not verify(raw, sign(raw, "some_other_secret"), SECRET)
    assert not verify(raw, None, SECRET)
    assert not verify(raw, "", SECRET)


def test_no_configured_secret_rejects_everything():
    """Fail closed. A blank secret must not become a blank cheque."""
    raw = _raw({"event": "payment.captured"})
    assert not verify(raw, sign(raw, ""), "")
    assert not verify(raw, "anything", "")


def test_receive_refuses_an_unsigned_delivery_and_says_so_in_the_audit_log():
    raw = _raw(payloads.payment_captured(
        payment_id="pay_x", amount=1000, record_id="REC_1"))

    reception = receive(raw, "not-a-signature", secret=SECRET)

    assert reception.status == 401
    assert reception.outcome == "INVALID_SIGNATURE"
    rejected = [r for r in audit.timeline(ORPHAN) if r.outcome == "REJECTED"]
    assert rejected, "a refused webhook left no trace"


def test_a_verified_delivery_is_accepted():
    intervention = _executed_intervention()
    with SessionLocal() as session:
        amount = session.get(AtRiskRecordRow, intervention.record_id).amount

    raw = _raw(payloads.payment_link_paid(
        link_id=intervention.razorpay_ref, payment_id="pay_ok", amount=amount,
        record_id=intervention.record_id))

    reception = receive(raw, sign(raw, SECRET), secret=SECRET)

    assert reception.status == 200
    assert reception.outcome == PROCESSED


# --- parsing -----------------------------------------------------------------


def test_every_handled_event_shape_parses():
    cases = [
        (payloads.payment_link_paid(link_id="plink_1", payment_id="pay_1",
                                    amount=1200, record_id="REC_1"),
         "payment_link.paid", "plink_1", True),
        (payloads.order_paid(order_id="order_1", payment_id="pay_2",
                             amount=1300, record_id="REC_2"),
         "order.paid", "order_1", True),
        (payloads.payment_captured(payment_id="pay_3", amount=1400,
                                   record_id="REC_3"),
         "payment.captured", "pay_3", True),
        (payloads.subscription_charged(subscription_id="sub_1", payment_id="pay_4",
                                       amount=1500, record_id="REC_4"),
         "subscription.charged", "sub_1", True),
        (payloads.payment_failed(payment_id="pay_5", amount=1600,
                                 record_id="REC_5"),
         "payment.failed", "pay_5", False),
    ]
    for body, event_type, ref, succeeded in cases:
        event = parse(body)
        assert event.event_type == event_type
        assert event.handled
        assert event.succeeded is succeeded
        assert ref in event.refs
        assert event.amount > 0
        assert event.record_id is not None


def test_a_malformed_body_is_rejected_without_raising():
    raw = b"{not json at all"
    reception = receive(raw, sign(raw, SECRET), secret=SECRET)
    assert reception.status == 400
    assert reception.attribution is None or reception.outcome != PROCESSED


def test_an_event_with_no_type_is_malformed_not_a_crash():
    raw = _raw({"entity": "event", "payload": {}})
    reception = receive(raw, sign(raw, SECRET), secret=SECRET)
    assert reception.status == 400


def test_an_unsubscribed_event_is_acknowledged_and_ignored():
    """200-then-ignore. Returning an error would just buy a retry of an event we
    have no opinion about."""
    raw = _raw({"entity": "event", "event": "payment.authorized",
                "payload": {"payment": {"entity": {"id": "pay_z", "amount": 100}}}})
    reception = receive(raw, sign(raw, SECRET), secret=SECRET)
    assert reception.status == 200
    assert reception.outcome == IGNORED


# --- attribution -------------------------------------------------------------


def test_a_paid_link_marks_its_record_recovered_and_attributes_the_money():
    intervention = _executed_intervention()
    with SessionLocal() as session:
        amount = session.get(AtRiskRecordRow, intervention.record_id).amount

    result = handle(payloads.payment_link_paid(
        link_id=intervention.razorpay_ref, payment_id="pay_ok", amount=amount,
        record_id=intervention.record_id))

    assert result.outcome == PROCESSED
    assert result.record_id == intervention.record_id
    assert result.amount == amount

    with SessionLocal() as session:
        record = session.get(AtRiskRecordRow, intervention.record_id)
        row = session.get(InterventionRow, intervention.id)
        assert record.state == RecordState.RECOVERED.value
        assert row.result == RESULT_RECOVERED
        assert row.recovered_amount == amount

    stages = [(r.stage, r.outcome) for r in audit.timeline(intervention.record_id)]
    assert (Stage.OUTCOME.value, RESULT_RECOVERED) in stages


def test_the_same_delivery_twice_is_handled_once():
    """Razorpay retries. A retry must not recover the same money twice."""
    intervention = _executed_intervention()
    with SessionLocal() as session:
        amount = session.get(AtRiskRecordRow, intervention.record_id).amount
    body = payloads.payment_link_paid(
        link_id=intervention.razorpay_ref, payment_id="pay_ok", amount=amount,
        record_id=intervention.record_id)

    first = handle(body, event_id="evt_same")
    second = handle(body, event_id="evt_same")

    assert first.outcome == PROCESSED
    assert second.outcome == DUPLICATE

    with SessionLocal() as session:
        total = sum(i.recovered_amount for i in session.query(InterventionRow)
                    .filter(InterventionRow.record_id == intervention.record_id))
    assert total == amount


def test_two_different_events_for_one_payment_attribute_once():
    """`payment.captured` and `payment_link.paid` describe the same rupees and
    arrive together. Counting both would inflate the scoreboard."""
    intervention = _executed_intervention()
    with SessionLocal() as session:
        amount = session.get(AtRiskRecordRow, intervention.record_id).amount

    first = handle(payloads.payment_link_paid(
        link_id=intervention.razorpay_ref, payment_id="pay_ok", amount=amount,
        record_id=intervention.record_id), event_id="evt_link")
    second = handle(payloads.payment_captured(
        payment_id="pay_ok", amount=amount, record_id=intervention.record_id),
        event_id="evt_captured")

    assert first.outcome == PROCESSED
    assert second.outcome == ALREADY_ATTRIBUTED

    with SessionLocal() as session:
        total = sum(i.recovered_amount for i in session.query(InterventionRow)
                    .filter(InterventionRow.record_id == intervention.record_id))
    assert total == amount


def test_an_event_matching_nothing_is_logged_loudly_rather_than_dropped():
    """Money we cannot explain is not money we get to count — but it is also
    not something to swallow in silence."""
    result = handle(payloads.payment_link_paid(
        link_id="plink_belongs_to_nobody", payment_id="pay_?", amount=9900,
        record_id="REC_does_not_exist"))

    assert result.outcome == UNATTRIBUTED
    orphans = [r for r in audit.timeline(ORPHAN) if r.outcome == UNATTRIBUTED]
    assert orphans


def test_a_failed_payment_spends_the_attempt_without_recovering():
    intervention = _executed_intervention()
    with SessionLocal() as session:
        record = session.get(AtRiskRecordRow, intervention.record_id)
        amount, attempts_before = record.amount, record.attempts

    result = handle(payloads.payment_failed(
        payment_id="pay_bad", amount=amount, record_id=intervention.record_id,
        order_id=intervention.razorpay_ref,
        description="Your card has insufficient balance."))

    assert result.outcome == PROCESSED

    with SessionLocal() as session:
        record = session.get(AtRiskRecordRow, intervention.record_id)
        row = session.get(InterventionRow, intervention.id)
        assert record.state != RecordState.RECOVERED.value
        assert record.attempts == attempts_before + 1
        assert row.result == RESULT_FAILED_AGAIN
        assert row.recovered_amount == 0


def test_attribution_never_credits_more_than_the_record_was_worth():
    """An overpayment is the merchant's problem, not a bigger recovery
    number."""
    intervention = _executed_intervention()
    with SessionLocal() as session:
        amount = session.get(AtRiskRecordRow, intervention.record_id).amount

    result = handle(payloads.payment_link_paid(
        link_id=intervention.razorpay_ref, payment_id="pay_big",
        amount=amount * 10, record_id=intervention.record_id))

    assert result.amount == amount


def test_an_event_can_be_attributed_by_notes_when_the_ref_is_unknown():
    """Razorpay notes carry the record id on every write we make. When a
    payment arrives against an id we did not mint — a customer paying a fresh
    link, say — the notes still tie it back."""
    intervention = _executed_intervention()
    with SessionLocal() as session:
        amount = session.get(AtRiskRecordRow, intervention.record_id).amount

    result = handle(payloads.payment_captured(
        payment_id="pay_unknown_id", amount=amount,
        record_id=intervention.record_id))

    assert result.outcome == PROCESSED
    assert result.record_id == intervention.record_id


def test_every_delivery_is_recorded_whatever_its_fate():
    handle(payloads.payment_link_paid(
        link_id="plink_nobody", payment_id="pay_?", amount=100,
        record_id="REC_nobody"), event_id="evt_a")
    handle({"entity": "event", "event": "payment.authorized", "payload": {}},
           event_id="evt_b")

    with SessionLocal() as session:
        rows = {r.event_id: r.outcome for r in session.query(WebhookEventRow)}
    assert rows["evt_a"] == UNATTRIBUTED
    assert rows["evt_b"] == IGNORED


# --- the loop, end to end ----------------------------------------------------


def test_the_batch_recovers_money_only_through_verified_webhooks():
    """The whole chain, in one assertion: a batch runs, outcomes come back as
    signed deliveries, and every recovered rupee on the scoreboard traces to an
    attributed webhook."""
    from reclaim.scoreboard import compute

    result = run_batch(dry_run=True)
    assert result.settlement is not None
    assert result.settlement.recovered > 0
    assert result.settlement.unattributed == 0

    board = compute()
    assert board.recovered_paise > 0
    assert board.balances

    with SessionLocal() as session:
        attributed = sum(
            i.recovered_amount for i in session.query(InterventionRow)
            .filter(InterventionRow.result == RESULT_RECOVERED))
        deliveries = session.query(WebhookEventRow).filter(
            WebhookEventRow.outcome == PROCESSED).count()

    assert board.recovered_paise == attributed
    assert deliveries >= board.recovered_records
