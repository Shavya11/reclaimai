"""The guardrail suite.

This file is a deliverable, not a nicety. "How do you know it cannot spam
someone or double-charge?" is answered by running this, not by claiming it.

The eight named tests come from PROJECT.md §7. The two property tests at the
bottom assert the invariants over randomly generated batches, because a
hand-written example only proves the case somebody thought of.
"""

from datetime import datetime, timedelta

import pytest
from hypothesis import HealthCheck, given, settings as hyp_settings, strategies as st

from reclaim.brain.guardrails import GUARDRAIL_NAMES, REGISTRY, GuardrailContext, evaluate_all
from reclaim.enums import ActionType, Channel, RecordState
from reclaim.models import ProposedAction
from reclaim.timeutil import IST

NOON = datetime(2026, 8, 24, 12, 0, tzinfo=IST)
THREE_AM = datetime(2026, 8, 24, 3, 0, tzinfo=IST)


def _action(action_type=ActionType.SEND_LINK, channel=Channel.SMS, when=NOON,
            attempt=1, amount=12400, record_id="R1"):
    return ProposedAction(
        record_id=record_id, action_type=action_type, channel=channel,
        scheduled_for=when, attempt_number=attempt, policy_ref="P.C",
        rationale="t", amount=amount,
    )


def _ctx(**kw):
    base = dict(now=NOON, autopilot_enabled=True, policy_max_attempts=3)
    base.update(kw)
    return GuardrailContext(**base)


# --- the eight named tests from PROJECT.md §7 -------------------------------


def test_opted_out_customer_never_contacted():
    result = evaluate_all(_action(), _ctx(opted_out=True))
    assert result.allowed is False
    assert "consent" in result.blocking_guardrails
    # Permanent, not deferred: rescheduling would still contact them.
    assert result.deferred_until is None


def test_silent_retry_allowed_at_3am_but_sms_is_not():
    silent = evaluate_all(
        _action(ActionType.SILENT_RETRY, channel=None, when=THREE_AM), _ctx())
    sms = evaluate_all(_action(ActionType.SEND_LINK, when=THREE_AM), _ctx())

    assert silent.allowed is True
    assert sms.allowed is False
    assert "quiet_hours" in sms.blocking_guardrails
    assert sms.deferred_until.hour == 9


def test_idempotency_blocks_replay():
    action = _action()
    fresh = evaluate_all(action, _ctx())
    replay = evaluate_all(action, _ctx(executed_keys=frozenset({action.idempotency_key})))

    assert fresh.allowed is True
    assert replay.allowed is False
    assert "idempotency" in replay.blocking_guardrails


def test_frequency_cap_spans_multiple_records():
    """Four records, one customer, cap of two. The cap counts contacts to the
    PERSON, so records three and four are blocked however different they are."""
    contacts = 0
    for i in range(4):
        action = _action(record_id=f"R{i}", channel=Channel.EMAIL)
        result = evaluate_all(action, _ctx(contacts_last_7d=contacts))
        if result.allowed:
            contacts += 1
        else:
            assert "frequency_cap" in result.blocking_guardrails
    assert contacts == 2


def test_high_value_requires_human():
    result = evaluate_all(_action(amount=7_800_000, channel=Channel.EMAIL), _ctx())
    assert result.allowed is False
    assert result.requires_human is True
    assert "value_ceiling" in result.blocking_guardrails


def test_risk_decline_never_produces_an_action():
    """Policy-level, not guardrail-level: the table refuses before the gate is
    even asked."""
    from reclaim.brain.policy import decide
    from reclaim.enums import LeakType, RootCause
    from reclaim.models import AtRiskRecord, Diagnosis

    record = AtRiskRecord(id="R", leak_type=LeakType.FAILED_PAYMENT, amount=1000,
                          counterparty_id="C", source_ref="s", detected_at=NOON)
    dx = Diagnosis(root_cause=RootCause.RISK_DECLINE, confidence=1.0, reasoning="t",
                   recoverable=False, evidence_used=[], source="deterministic")
    action = decide(record, dx, frm=NOON)
    assert action.action_type is ActionType.ESCALATE


def test_kill_switch_stops_everything():
    for action_type in ActionType:
        result = evaluate_all(
            _action(action_type, channel=None), _ctx(autopilot_enabled=False))
        assert result.allowed is False
        assert "kill_switch" in result.blocking_guardrails


def test_guardrails_never_raise():
    """Fuzz malformed contexts. Every one must BLOCK, not throw. A guardrail
    that throws is one somebody eventually wraps in `except: pass`."""
    import random

    rng = random.Random(42)
    junk = [None, "", -1, 10**9, float("nan"), object(), [], {}]
    threw = set()
    for _ in range(500):
        ctx = _ctx(
            opted_out=rng.choice(junk), on_dnd=rng.choice(junk),
            contacts_last_7d=rng.choice(junk), executed_keys=rng.choice(junk),
            record_state=rng.choice(junk), record_age_days=rng.choice(junk),
            diagnosis_confidence=rng.choice(junk), actions_today=rng.choice(junk),
            policy_max_attempts=rng.choice(junk), last_contact_at=rng.choice(junk),
        )
        result = evaluate_all(_action(), ctx)  # must not raise
        # Fail CLOSED: garbage in must never yield permission to act.
        assert result.allowed is False
        threw.update(v.guardrail for v in result.violations if "raised" in v.reason)

    # Proof the fuzz is not vacuous: real guardrails did throw and were caught.
    assert len(threw) >= 5


# --- engine behaviour -------------------------------------------------------


def test_all_thirteen_guardrails_are_registered():
    assert len(REGISTRY) == 13
    assert len(set(GUARDRAIL_NAMES)) == 13


def test_all_violations_are_collected_not_just_the_first():
    """Short-circuiting would hide reasons from the audit trail."""
    result = evaluate_all(
        _action(when=THREE_AM, amount=9_000_000),
        _ctx(opted_out=True, on_dnd=True, contacts_last_7d=5),
    )
    assert len(result.violations) >= 4
    for name in ("consent", "dnd", "quiet_hours", "frequency_cap", "value_ceiling"):
        assert name in result.blocking_guardrails


def test_a_throwing_guardrail_blocks_rather_than_being_skipped():
    class Exploding:
        name = "exploding"

        def check(self, action, ctx):
            raise RuntimeError("boom")

    result = evaluate_all(_action(), _ctx(), guardrails=[Exploding()])
    assert result.allowed is False
    assert "exploding" in result.blocking_guardrails


def test_blocked_is_not_dropped():
    """Every block carries what happens next."""
    deferred = evaluate_all(_action(when=THREE_AM), _ctx())
    human = evaluate_all(_action(amount=9_000_000, channel=Channel.EMAIL), _ctx())
    permanent = evaluate_all(_action(), _ctx(opted_out=True))

    assert deferred.deferred_until is not None
    assert human.requires_human is True
    assert permanent.deferred_until is None  # permanent stop, not a reschedule


def test_permanent_block_outranks_a_deferral():
    result = evaluate_all(_action(when=THREE_AM), _ctx(opted_out=True))
    assert result.deferred_until is None


def test_clean_action_is_allowed():
    assert evaluate_all(_action(channel=Channel.EMAIL), _ctx()).allowed is True


# --- individual rules -------------------------------------------------------


def test_dnd_blocks_sms_but_allows_email():
    assert evaluate_all(_action(channel=Channel.SMS), _ctx(on_dnd=True)).allowed is False
    assert evaluate_all(_action(channel=Channel.EMAIL), _ctx(on_dnd=True)).allowed is True


def test_cooldown_defers_to_the_end_of_the_window():
    last = NOON - timedelta(hours=3)
    result = evaluate_all(_action(channel=Channel.EMAIL), _ctx(last_contact_at=last))
    assert result.allowed is False
    assert result.deferred_until == last + timedelta(hours=24)


def test_low_confidence_routes_to_a_human():
    result = evaluate_all(_action(channel=Channel.EMAIL), _ctx(diagnosis_confidence=0.4))
    assert result.requires_human is True
    assert "confidence_floor" in result.blocking_guardrails


def test_recovered_record_is_not_chased():
    result = evaluate_all(_action(), _ctx(record_state=RecordState.RECOVERED.value))
    assert result.allowed is False
    assert "state_validity" in result.blocking_guardrails


def test_stale_record_is_closed():
    result = evaluate_all(_action(channel=Channel.EMAIL), _ctx(record_age_days=120))
    assert result.allowed is False
    assert "freshness" in result.blocking_guardrails


def test_global_hard_cap_beats_a_permissive_policy():
    """A misconfigured YAML row must not be able to authorise harassment."""
    result = evaluate_all(_action(attempt=5), _ctx(policy_max_attempts=99))
    assert result.allowed is False
    assert "max_attempts" in result.blocking_guardrails


def test_silent_retry_is_exempt_from_contact_rules():
    silent = _action(ActionType.SILENT_RETRY, channel=None, when=THREE_AM)
    result = evaluate_all(silent, _ctx(on_dnd=True, contacts_last_7d=9,
                                       last_contact_at=NOON - timedelta(hours=1)))
    assert result.allowed is True


# --- property tests: the two invariants -------------------------------------


@hyp_settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow],
              deadline=None)
@given(
    n_records=st.integers(min_value=1, max_value=12),
    attempts=st.lists(st.integers(min_value=1, max_value=3), min_size=1, max_size=12),
    channel=st.sampled_from([Channel.SMS, Channel.EMAIL, Channel.WHATSAPP]),
)
def test_invariant_no_customer_exceeds_two_contacts_in_seven_days(
    n_records, attempts, channel
):
    """INVARIANT 1: however many records one customer owns, and in whatever
    order they are processed, they receive at most two contacts per 7 days."""
    contacts = 0
    for i in range(n_records):
        for attempt in attempts[:3]:
            action = _action(record_id=f"R{i}", channel=channel, attempt=attempt)
            result = evaluate_all(action, _ctx(contacts_last_7d=contacts))
            if result.allowed and action.action_type.contacts_customer:
                contacts += 1
    assert contacts <= 2


@hyp_settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow],
              deadline=None)
@given(
    keys=st.lists(
        st.tuples(
            st.text(alphabet="ABCDEFG", min_size=1, max_size=3),
            st.integers(min_value=1, max_value=3),
            st.sampled_from([ActionType.SEND_LINK, ActionType.NOTIFY,
                             ActionType.SILENT_RETRY]),
        ),
        min_size=1, max_size=30,
    )
)
def test_invariant_no_action_tuple_ever_executes_twice(keys):
    """INVARIANT 2: (record_id, attempt_number, action_type) executes at most
    once, no matter how many times it is replayed or in what order."""
    executed: set[str] = set()
    for record_id, attempt, action_type in keys:
        action = _action(action_type, channel=Channel.EMAIL, record_id=record_id,
                         attempt=attempt)
        result = evaluate_all(action, _ctx(executed_keys=frozenset(executed)))
        if result.allowed:
            assert action.idempotency_key not in executed
            executed.add(action.idempotency_key)

    assert len(executed) == len(set(executed))
