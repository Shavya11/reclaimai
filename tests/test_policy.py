"""Policy engine and schedule resolver.

The rows that matter most here are the ones that do nothing. Five causes resolve
to no_auto_action, and a system that quietly starts acting on them is a system
that retries risk declines until the merchant loses its account.
"""

from datetime import datetime

import pytest

from reclaim.brain import rules
from reclaim.brain.policy import decide
from reclaim.brain.policy.engine import STRATEGY_TO_ACTION, prefill_method
from reclaim.brain.policy.schedule import ScheduleError, resolve
from reclaim.enums import (
    ActionType, CAUSES_FOR_LEAK, Channel, LeakType, RecordState, RootCause,
)
from reclaim.models import AtRiskRecord, Diagnosis
from reclaim.timeutil import IST

NOON = datetime(2026, 8, 24, 12, 0, tzinfo=IST)


def _record(leak=LeakType.FAILED_PAYMENT, amount=12400, attempts=0):
    return AtRiskRecord(
        id="R1", leak_type=leak, amount=amount, counterparty_id="C1",
        source_ref="pay_1", detected_at=NOON, attempts=attempts,
        state=RecordState.AT_RISK, raw_signals={"issuer_bank": "HDFC"},
    )


def _dx(cause, confidence=1.0):
    return Diagnosis(root_cause=cause, confidence=confidence, reasoning="t",
                     recoverable=True, evidence_used=[], source="deterministic")


# --- rules load through one module -----------------------------------------


def test_every_reachable_cause_has_a_policy_row():
    """Coverage is per leak type, not global.

    EXPIRED_INSTRUMENT cannot happen to an invoice and INVOICE_DISPUTED cannot
    happen to a card, so asserting one flat list would force nonsense rows into
    the table. CAUSES_FOR_LEAK says what is actually reachable, and this asserts
    the table covers exactly that — no gaps, and no rows for combinations that
    can never occur."""
    for leak, causes in CAUSES_FOR_LEAK.items():
        table = rules.policies()[leak.value]
        missing = sorted(c.value for c in causes if c.value not in table)
        extra = sorted(k for k in table if k not in {c.value for c in causes})
        assert missing == [], f"{leak.value} has no row for {missing}"
        assert extra == [], f"{leak.value} has unreachable rows {extra}"


def test_every_leak_type_falls_back_to_unknown():
    """An unmapped combination must escalate to a human, never silently do
    nothing. policy_for() relies on an UNKNOWN row existing to do that."""
    for leak in CAUSES_FOR_LEAK:
        assert rules.policy_for(leak.value, "NOT_A_REAL_CAUSE") is not None


def test_every_policy_row_states_a_rationale():
    """The audit trail quotes this back. A row without one produces a decision
    nobody can explain after the fact."""
    for leak, table in rules.policies().items():
        for cause, row in table.items():
            assert row.get("rationale"), f"{leak}.{cause} has no rationale"


def test_every_strategy_maps_to_an_action():
    for leak, table in rules.policies().items():
        for cause, row in table.items():
            assert row["strategy"] in STRATEGY_TO_ACTION, f"{leak}.{cause}"


def test_unmapped_combination_falls_back_to_a_human():
    row = rules.policy_for("FAILED_PAYMENT", "A_CAUSE_THAT_DOES_NOT_EXIST")
    assert row["strategy"] == "no_auto_action"


# --- the rows that refuse to act -------------------------------------------


@pytest.mark.parametrize("cause", [
    RootCause.RISK_DECLINE,
    RootCause.MANDATE_REVOKED,
    RootCause.POLICY_BLOCK,
    RootCause.UNKNOWN,
])
def test_never_retry_causes_produce_no_automated_action(cause):
    action = decide(_record(), _dx(cause), frm=NOON)
    assert action.action_type is ActionType.ESCALATE
    assert action.channel is None


def test_risk_decline_never_produces_a_retry():
    """Retries against a risk decline look like card testing to an issuer."""
    action = decide(_record(), _dx(RootCause.RISK_DECLINE), frm=NOON)
    assert action.action_type not in {ActionType.RETRY, ActionType.SILENT_RETRY}


# --- the rows that act ------------------------------------------------------


def test_bank_downtime_retries_without_contacting_anyone():
    """The headline restraint: an issuer outage is not the customer's problem."""
    action = decide(_record(), _dx(RootCause.BANK_DOWNTIME), frm=NOON)
    assert action.action_type is ActionType.SILENT_RETRY
    assert action.channel is None
    assert action.action_type.contacts_customer is False


def test_insufficient_funds_waits_for_the_salary_window():
    action = decide(_record(), _dx(RootCause.INSUFFICIENT_FUNDS), frm=NOON)
    assert action.scheduled_for.day == 1
    assert action.scheduled_for.month == 9
    assert action.channel is Channel.WHATSAPP


def test_expired_card_asks_for_a_new_method_not_a_retry():
    action = decide(_record(), _dx(RootCause.EXPIRED_INSTRUMENT), frm=NOON)
    assert action.action_type is ActionType.SEND_LINK
    assert prefill_method(action.policy_ref) == "upi"


def test_auth_dropoff_switches_away_from_the_step_they_abandoned():
    action = decide(_record(), _dx(RootCause.AUTH_DROPOFF), frm=NOON)
    assert prefill_method(action.policy_ref) == "upi"


# --- traceability and stopping ---------------------------------------------


def test_policy_ref_identifies_the_row_that_decided():
    action = decide(_record(), _dx(RootCause.BANK_DOWNTIME), frm=NOON)
    assert action.policy_ref == "FAILED_PAYMENT.BANK_DOWNTIME"


def test_exhausting_max_attempts_stops_rather_than_looping():
    action = decide(_record(attempts=3), _dx(RootCause.BANK_DOWNTIME), frm=NOON)
    assert action.action_type is ActionType.NO_ACTION


def test_attempt_number_increments_from_the_record():
    action = decide(_record(attempts=1), _dx(RootCause.BANK_DOWNTIME), frm=NOON)
    assert action.attempt_number == 2
    assert action.idempotency_key == "R1:2:SILENT_RETRY"


def test_later_attempts_are_scheduled_later():
    first = decide(_record(attempts=0), _dx(RootCause.BANK_DOWNTIME), frm=NOON)
    second = decide(_record(attempts=1), _dx(RootCause.BANK_DOWNTIME), frm=NOON)
    assert second.scheduled_for > first.scheduled_for


def test_leak_type_selects_a_different_table():
    action = decide(_record(leak=LeakType.ABANDONED_CART),
                    _dx(RootCause.CART_ABANDONMENT), frm=NOON)
    assert action.policy_ref == "ABANDONED_CART.CART_ABANDONMENT"
    assert action.action_type is ActionType.SEND_LINK


# --- schedule resolver ------------------------------------------------------


@pytest.mark.parametrize("token,expect", [
    ("immediate", NOON),
    ("20m", datetime(2026, 8, 24, 12, 20, tzinfo=IST)),
    ("2h", datetime(2026, 8, 24, 14, 0, tzinfo=IST)),
    ("+7d", datetime(2026, 8, 31, 12, 0, tzinfo=IST)),
])
def test_relative_tokens(token, expect):
    assert resolve(token, NOON) == expect


def test_day_at_time_token():
    got = resolve("+1d@10:00", NOON)
    assert (got.day, got.hour, got.minute) == (25, 10, 0)


def test_salary_window_token():
    got = resolve("next_salary_window", NOON)
    assert (got.month, got.day, got.hour) == (9, 1, 11)


def test_unknown_token_is_loud_not_silent():
    with pytest.raises(ScheduleError):
        resolve("whenever", NOON)


def test_schedules_resolve_from_a_given_time_not_the_wall_clock():
    """Day 3's time-travel demo depends on this: pretend it is the 1st and the
    salary-window retries become observable in five minutes."""
    early = datetime(2026, 8, 1, 9, 0, tzinfo=IST)
    assert resolve("next_salary_window", early).day == 1
    assert resolve("next_salary_window", early).hour == 11
