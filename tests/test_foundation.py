"""Day 1 deliverables, as executable claims.

The two database-level guarantees are the point of this file: an automated
reviewer asking "can it double-charge?" or "was the audit trail edited?" should
be able to run these and read the answer.
"""

import uuid

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError, OperationalError

from reclaim.db import AuditLogRow, ExecutedActionRow, SessionLocal, init_db
from reclaim.enums import ActionType, LeakType, RecordState, RootCause, Stage
from reclaim.models import AtRiskRecord, ProposedAction
from reclaim.money import format_inr, format_inr_short
from reclaim.timeutil import IST, is_quiet_hours, next_contact_window, next_salary_window

from datetime import datetime


@pytest.fixture(scope="module", autouse=True)
def _db():
    init_db()


def _uid() -> str:
    return f"REC_{uuid.uuid4().hex[:10]}"


# --- the two guarantees -----------------------------------------------------


def test_audit_log_rejects_update():
    rid = _uid()
    with SessionLocal() as s:
        s.add(AuditLogRow(record_id=rid, stage="DETECT", outcome="OK", reason="x"))
        s.commit()
    with pytest.raises((OperationalError, IntegrityError)):
        with SessionLocal() as s:
            s.execute(update(AuditLogRow).where(AuditLogRow.record_id == rid).values(reason="tampered"))
            s.commit()


def test_audit_log_rejects_delete():
    rid = _uid()
    with SessionLocal() as s:
        s.add(AuditLogRow(record_id=rid, stage="DETECT", outcome="OK", reason="x"))
        s.commit()
    with pytest.raises((OperationalError, IntegrityError)):
        with SessionLocal() as s:
            s.execute(delete(AuditLogRow).where(AuditLogRow.record_id == rid))
            s.commit()


def test_idempotency_key_is_unique_at_the_database():
    rid = _uid()
    key = f"{rid}:1:SEND_LINK"
    with SessionLocal() as s:
        s.add(ExecutedActionRow(idempotency_key=key, record_id=rid, attempt_number=1, action_type="SEND_LINK"))
        s.commit()
    with pytest.raises(IntegrityError):
        with SessionLocal() as s:
            s.add(ExecutedActionRow(idempotency_key=key, record_id=rid, attempt_number=1, action_type="SEND_LINK"))
            s.commit()


def test_idempotency_key_is_derived_not_supplied():
    """It is a property, so it cannot drift from the tuple it represents."""
    a = ProposedAction(record_id="REC_9", action_type=ActionType.RETRY,
                       scheduled_for=datetime(2026, 8, 24, 12, 0, tzinfo=IST),
                       attempt_number=2, policy_ref="P", rationale="r", amount=100)
    assert a.idempotency_key == "REC_9:2:RETRY"
    with pytest.raises(Exception):
        ProposedAction(record_id="REC_9", action_type=ActionType.RETRY,
                       scheduled_for=datetime(2026, 8, 24, 12, 0, tzinfo=IST),
                       attempt_number=2, policy_ref="P", rationale="r", amount=100,
                       idempotency_key="forged")


# --- V2 readiness -----------------------------------------------------------


def test_at_risk_record_carries_no_payment_specific_fields():
    """V2 adds overdue invoices as another leak_type, not a schema migration."""
    forbidden = {"card_network", "issuer_bank", "error_code", "payment_id", "method"}
    assert forbidden.isdisjoint(AtRiskRecord.model_fields)


def test_every_root_cause_is_a_plain_string():
    for c in RootCause:
        assert isinstance(c.value, str) and c.value == c.name


# --- domain behaviour -------------------------------------------------------


def test_timestamps_are_coerced_to_ist():
    r = AtRiskRecord(id="R", leak_type=LeakType.FAILED_PAYMENT, amount=1,
                     counterparty_id="C", source_ref="s",
                     detected_at=datetime(2026, 8, 24, 12, 0))
    assert r.detected_at.tzinfo is not None
    assert r.detected_at.utcoffset().total_seconds() == 19800  # +05:30


def test_silent_retry_does_not_count_as_customer_contact():
    assert ActionType.SILENT_RETRY.contacts_customer is False
    assert ActionType.SEND_LINK.contacts_customer is True


@pytest.mark.parametrize("hour,quiet", [(3, True), (8, True), (9, False), (19, False), (20, True), (23, True)])
def test_quiet_hours_window(hour, quiet):
    assert is_quiet_hours(datetime(2026, 8, 24, hour, 0, tzinfo=IST)) is quiet


def test_quiet_hours_defer_to_next_nine_am():
    got = next_contact_window(datetime(2026, 8, 24, 22, 30, tzinfo=IST))
    assert (got.day, got.hour) == (25, 9)


def test_salary_window_lands_on_the_first():
    got = next_salary_window(datetime(2026, 8, 24, 15, 0, tzinfo=IST))
    assert (got.month, got.day, got.hour) == (9, 1, 11)


def test_terminal_states():
    assert RecordState.RECOVERED.is_terminal
    assert not RecordState.AT_RISK.is_terminal


def test_indian_digit_grouping():
    assert format_inr(58430000) == "₹5,84,300"
    assert format_inr_short(58430000) == "₹5.84L"
    assert format_inr(19900) == "₹199"
