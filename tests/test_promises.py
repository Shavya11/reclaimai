"""The promise-to-pay state machine, and guardrail 14 above it.

The behaviour under test is a system deliberately doing nothing. That is hard to
see and easy to break — a promise that silently fails to suppress contact looks
identical to one that works, right up until a customer who said "Friday" gets
dunned on Wednesday. So these tests assert the silence directly.
"""

from datetime import timedelta

import pytest

from reclaim import clock, promises
from reclaim.brain.guardrails import GuardrailContext, evaluate_all
from reclaim.brain.guardrails.rules.promise_window import PromiseWindow
from reclaim.db import (
    AtRiskRecordRow, CustomerRow, PromiseRow, SessionLocal, reset_database,
)
from reclaim.enums import ActionType, Channel, LeakType, PromiseState, RecordState
from reclaim.models import AtRiskRecord, ProposedAction
from reclaim.repository import save_batch
from reclaim.timeutil import to_ist


@pytest.fixture(autouse=True)
def _clean():
    reset_database()
    clock.reset()


def _now():
    return clock.now().replace(hour=11, minute=0, second=0, microsecond=0)


def _record(rid="INV_7000", amount=250_000_00):
    return AtRiskRecord(
        id=rid, leak_type=LeakType.OVERDUE_INVOICE, amount=amount,
        counterparty_id="BUYER_9000", source_ref="inv_1",
        detected_at=_now() - timedelta(days=30),
        due_at=_now() - timedelta(days=30),
        raw_signals={"days_overdue": 30, "buyer_org": "Kaveri Logistics"},
    )


class _Customer:
    def __init__(self, cid="BUYER_9000"):
        self.id = cid
        self.email = "accounts@kaveri.example.com"
        self.phone = "+918000000000"
        self.opted_out = False
        self.on_dnd = False
        self.successful_payments_lifetime = 12
        self.last_successful_at = None


def _store(record):
    save_batch([record], [_Customer(record.counterparty_id)])


def _action(rid="INV_7000", action_type=ActionType.SEND_LINK, when=None,
            amount=250_000_00):
    return ProposedAction(
        record_id=rid, action_type=action_type, channel=Channel.EMAIL,
        scheduled_for=when or _now(), attempt_number=1,
        policy_ref="OVERDUE_INVOICE.PAYMENT_STALLED", rationale="t",
        amount=amount,
    )


def _ctx(promised_for=None, **kw):
    return GuardrailContext(now=_now(),
                            extra={"promised_for": promised_for}, **kw)


# --- date validation: the model reads, the system decides -------------------


def test_a_future_date_inside_the_horizon_is_accepted():
    when, why = promises.validate_date(_now() + timedelta(days=6), frm=_now())
    assert when is not None and why == ""


@pytest.mark.parametrize("offset_days", [-1, -30])
def test_a_date_in_the_past_is_refused(offset_days):
    when, why = promises.validate_date(
        _now() + timedelta(days=offset_days), frm=_now())
    assert when is None
    assert "not in the future" in why


def test_a_date_beyond_the_horizon_is_refused():
    """The model may extract "we'll pay in March". Acting on it would park a
    receivable for a quarter, so the system will not."""
    when, why = promises.validate_date(_now() + timedelta(days=200), frm=_now())
    assert when is None
    assert "horizon" in why


def test_no_date_is_refused_rather_than_invented():
    when, why = promises.validate_date(None, frm=_now())
    assert when is None and why


# --- the state machine ------------------------------------------------------


def test_recording_a_promise_puts_the_record_to_sleep_until_the_date():
    record = _record()
    _store(record)
    due = _now() + timedelta(days=5)

    promises.record_promise(record.id, promised_for=due, amount=record.amount,
                            intent="PROMISE_TO_PAY", confidence=0.9,
                            reply_text="friday tak ho jayega", at=_now())

    with SessionLocal() as session:
        row = session.get(AtRiskRecordRow, record.id)
        assert row.state == RecordState.PROMISED.value
        # SQLite hands back a naive datetime; every reader in the codebase
        # normalises through to_ist, so the test compares the way the code does.
        assert to_ist(row.next_action_at) == due


def test_a_second_promise_supersedes_the_first_rather_than_stacking():
    """A buyer who promises twice has MOVED their date, not made two
    commitments. Two open promises would leave one to fire a breach that never
    happened."""
    record = _record()
    _store(record)
    promises.record_promise(record.id, promised_for=_now() + timedelta(days=3),
                            amount=record.amount, intent="PROMISE_TO_PAY",
                            confidence=0.9, reply_text="wednesday", at=_now())
    promises.record_promise(record.id, promised_for=_now() + timedelta(days=9),
                            amount=record.amount, intent="PROMISE_TO_PAY",
                            confidence=0.9, reply_text="next friday", at=_now())

    assert len(promises.open_promises()) == 1
    with SessionLocal() as session:
        states = [r.state for r in session.query(PromiseRow)
                  .order_by(PromiseRow.id)]
    assert states == [PromiseState.BROKEN.value, PromiseState.OPEN.value]


def test_a_promise_kept_is_decided_by_the_money_not_by_the_customer():
    """Taking somebody's word for it is how a scoreboard starts counting
    sentences as rupees. A promise is KEPT only if the record actually
    recovered."""
    record = _record()
    _store(record)
    due = _now() + timedelta(days=2)
    promises.record_promise(record.id, promised_for=due, amount=record.amount,
                            intent="PROMISE_TO_PAY", confidence=0.9,
                            reply_text="will pay", at=_now())

    with SessionLocal() as session:
        session.get(AtRiskRecordRow, record.id).state = \
            RecordState.RECOVERED.value
        session.commit()

    kept, broken = promises.settle_due(at=due + timedelta(hours=1))
    assert kept == [record.id] and broken == []


def test_a_broken_promise_wakes_the_record_rather_than_closing_it():
    record = _record()
    _store(record)
    due = _now() + timedelta(days=2)
    promises.record_promise(record.id, promised_for=due, amount=record.amount,
                            intent="PROMISE_TO_PAY", confidence=0.9,
                            reply_text="will pay", at=_now())

    kept, broken = promises.settle_due(at=due + timedelta(hours=1))
    assert broken == [record.id] and kept == []

    with SessionLocal() as session:
        assert session.get(AtRiskRecordRow, record.id).state == \
            RecordState.AT_RISK.value
    assert promises.open_promises() == {}


def test_a_promise_not_yet_due_is_left_alone():
    record = _record()
    _store(record)
    promises.record_promise(record.id, promised_for=_now() + timedelta(days=10),
                            amount=record.amount, intent="PROMISE_TO_PAY",
                            confidence=0.9, reply_text="later", at=_now())

    assert promises.settle_due(at=_now() + timedelta(days=1)) == ([], [])
    assert len(promises.open_promises()) == 1


# --- guardrail 14 -----------------------------------------------------------


def test_contact_is_blocked_while_a_promise_stands():
    due = _now() + timedelta(days=5)
    violation = PromiseWindow().check(_action(), _ctx(promised_for=due))
    assert violation is not None
    assert violation.guardrail == "promise_window"
    assert violation.deferred_until is not None
    assert not violation.permanent, "a promise expires; it is not a permanent stop"
    assert not violation.closes_record


def test_a_silent_retry_is_exempt_because_it_never_reaches_the_person():
    """Chasing the money quietly while staying off somebody's phone is exactly
    the distinction this system makes everywhere else."""
    due = _now() + timedelta(days=5)
    action = _action(action_type=ActionType.SILENT_RETRY)
    assert PromiseWindow().check(action, _ctx(promised_for=due)) is None


def test_contact_resumes_after_the_date_plus_grace():
    due = _now() + timedelta(days=2)
    rail = PromiseWindow()

    on_the_day = _action(when=due + timedelta(hours=1))
    assert rail.check(on_the_day, _ctx(promised_for=due)) is not None, \
        "a promise for Friday must not be dunned on Friday morning"

    after_grace = _action(when=due + timedelta(hours=25))
    assert rail.check(after_grace, _ctx(promised_for=due)) is None


def test_no_promise_means_no_opinion():
    assert PromiseWindow().check(_action(), _ctx(promised_for=None)) is None


def test_the_promise_guardrail_never_raises_on_junk():
    """Fails closed like every other guardrail: junk blocks, it does not throw."""
    for junk in ("not-a-date", 12345, object(), [], {}):
        result = evaluate_all(_action(),
                              GuardrailContext(now=_now(),
                                               extra={"promised_for": junk}))
        assert isinstance(result.allowed, bool)


def test_the_promise_shows_up_in_the_collected_violations():
    """All violations, not the first — the audit trail is only worth reading if
    a blocked action says everything that blocked it."""
    due = _now() + timedelta(days=5)
    result = evaluate_all(
        _action(amount=90_00_00_000),
        _ctx(promised_for=due, opted_out=True),
    )
    assert not result.allowed
    assert "promise_window" in result.blocking_guardrails
    assert "consent" in result.blocking_guardrails
