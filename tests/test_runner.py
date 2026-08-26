"""The batch orchestrator, end to end.

The crash-and-resume test is the one PROJECT.md §14 says payments judges care
about more than anything. It is not a claim that the agent cannot double-charge;
it kills a batch mid-flight, restarts it, and counts the keys.
"""

from collections import Counter

import pytest
from sqlalchemy import func

from reclaim import audit, clock
from reclaim.db import (
    ExecutedActionRow, HumanQueueRow, SessionLocal, reset_database,
)
from reclaim.enums import Stage
from reclaim.runner import BatchCrashed, run_batch, tick


@pytest.fixture(autouse=True)
def _clean_db():
    """A whole new database per test. Wiping only the mutable tables leaves
    records escalated and diagnoses already logged, so the run under test
    silently becomes a resumed one and starts depending on test order."""
    reset_database()
    clock.reset()


def _duplicate_keys() -> list[str]:
    with SessionLocal() as s:
        return [k for (k,) in s.query(ExecutedActionRow.idempotency_key)
                .group_by(ExecutedActionRow.idempotency_key)
                .having(func.count("*") > 1).all()]


# --- the guarantee ----------------------------------------------------------


def test_a_crashed_batch_resumes_without_double_charging():
    """THE test. Kill the batch at 30 actions, restart it, count the keys."""
    with pytest.raises(BatchCrashed):
        run_batch(dry_run=True, crash_at=30)

    with SessionLocal() as s:
        claimed_before = s.query(ExecutedActionRow).count()
    assert claimed_before > 0, "crash landed before anything executed"

    run_batch(dry_run=True, reseed=False)

    assert _duplicate_keys() == []


def test_resume_blocks_replays_at_the_guardrail():
    """Defence in depth, layer one: guardrail #10 catches the replay before it
    ever reaches Razorpay, rather than letting the database constraint be the
    only thing standing between a crash and a second charge."""
    with pytest.raises(BatchCrashed):
        run_batch(dry_run=True, crash_at=30, settle=False)
    with SessionLocal() as s:
        claimed = {k for (k,) in s.query(ExecutedActionRow.idempotency_key)}
    assert claimed

    resumed = run_batch(dry_run=True, reseed=False, settle=False)

    # The resume is expected to execute the actions the crash never reached.
    # What it must not do is re-execute the ones it did, and the guardrail is
    # what stops that - the database constraint is the layer below.
    assert resumed.blocked_by["idempotency"] > 0

    with SessionLocal() as s:
        rows = Counter(k for (k,) in s.query(ExecutedActionRow.idempotency_key))
    assert set(claimed) <= set(rows), "a claimed key vanished on resume"
    assert all(rows[k] == 1 for k in claimed), "a claimed key was claimed twice"
    assert _duplicate_keys() == []


def test_running_the_same_batch_twice_executes_nothing_new():
    """A replay is a replay. settle=False freezes the world between the two
    runs, so nothing has legitimately advanced and every action the second run
    proposes is one the first already took."""
    first = run_batch(dry_run=True, settle=False)
    second = run_batch(dry_run=True, reseed=False, settle=False)

    assert first.executed > 0
    assert second.executed == 0
    assert _duplicate_keys() == []


def test_a_settled_outcome_advances_the_ladder_rather_than_replaying():
    """The other half of the same coin. Once outcomes come back, an unanswered
    record is on attempt 2 — a different action with a different key, not a
    replay of attempt 1. A system that cannot tell those apart either
    double-charges or never follows up."""
    first = run_batch(dry_run=True)
    assert first.settlement is not None
    assert first.settlement.no_response > 0, "nothing was left unanswered"

    # The follow-up is scheduled hours or days out by policy, so it takes a
    # tick to become due. That delay is the feature, not an obstacle.
    second, _ = tick(advance="48h", dry_run=True)

    assert second.executed > 0, "the follow-up rung never fired"
    assert _duplicate_keys() == []

    with SessionLocal() as s:
        attempts = {n for (n,) in s.query(ExecutedActionRow.attempt_number).all()}
    assert attempts > {1}, "every execution was still attempt 1"


def test_no_key_ever_appears_twice_across_many_runs():
    run_batch(dry_run=True)
    for _ in range(3):
        run_batch(dry_run=True, reseed=False)

    with SessionLocal() as s:
        total = s.query(ExecutedActionRow).count()
        distinct = s.query(
            func.count(func.distinct(ExecutedActionRow.idempotency_key))).scalar()
    assert total == distinct


# --- pipeline behaviour -----------------------------------------------------


def test_batch_completes_for_every_record():
    result = run_batch(dry_run=True)
    assert result.proposed == 120
    assert result.allowed + result.blocked == 120


def test_blocked_actions_are_audited_as_loudly_as_executed_ones():
    run_batch(dry_run=True)
    with SessionLocal() as s:
        from reclaim.db import AuditLogRow
        blocked = s.query(AuditLogRow).filter_by(
            stage=Stage.GUARDRAIL.value, outcome="BLOCKED").count()
        executed = s.query(AuditLogRow).filter_by(
            stage=Stage.EXECUTE.value, outcome="EXECUTED").count()
    assert blocked > 0
    assert executed > 0


def test_every_record_gets_a_diagnosis_and_a_decision_in_the_audit_log():
    run_batch(dry_run=True)
    with SessionLocal() as s:
        from reclaim.db import AuditLogRow
        diagnosed = s.query(func.count(func.distinct(AuditLogRow.record_id))).filter(
            AuditLogRow.stage == Stage.DIAGNOSE.value).scalar()
        decided = s.query(func.count(func.distinct(AuditLogRow.record_id))).filter(
            AuditLogRow.stage == Stage.DECIDE.value).scalar()
    assert diagnosed == 120
    assert decided == 120


def test_human_escalations_reach_a_queue_not_the_void():
    run_batch(dry_run=True)
    with SessionLocal() as s:
        assert s.query(HumanQueueRow).count() > 0


def test_one_record_has_a_readable_timeline():
    """Demo beat #3: click a record, see the whole decision."""
    run_batch(dry_run=True)
    rows = audit.timeline("REC_5000")
    stages = [r.stage for r in rows]
    assert Stage.DIAGNOSE.value in stages
    assert Stage.DECIDE.value in stages
    assert Stage.GUARDRAIL.value in stages
    for row in rows:
        assert row.reason, "an audit row without a reason explains nothing"


def test_a_dead_razorpay_parks_records_instead_of_crashing():
    """Demo beat #6: one failure handled gracefully."""
    from reclaim.executor.razorpay_client import RazorpayClient, RazorpayError

    class Dead(RazorpayClient):
        def __init__(self, *a, **kw):
            super().__init__(dry_run=True)

        def create_payment_link(self, *a, **kw):
            raise RazorpayError("connection refused")

        def create_order(self, *a, **kw):
            raise RazorpayError("connection refused")

    import reclaim.runner as runner_mod
    original = runner_mod.RazorpayClient
    runner_mod.RazorpayClient = Dead
    try:
        result = run_batch(dry_run=True)
    finally:
        runner_mod.RazorpayClient = original

    assert result.failed > 0
    assert result.proposed == 120       # the batch still completed
    assert _duplicate_keys() == []      # and still did not double-charge
