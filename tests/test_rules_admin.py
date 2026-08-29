"""Merchant-editable rules, and the what-if replay above them.

Two things are being defended. That a merchant cannot type a rule which takes
tonight's batch down — the validator is the only thing between a text box and
the decision engine. And that the replay, which exists to explain the numbers,
cannot alter them.
"""

import pytest

from reclaim import admin, clock, whatif
from reclaim.brain import rules
from reclaim.brain.validation import (
    RuleInvalid, validate_guardrail_config, validate_policy_row,
)
from reclaim.db import (
    AtRiskRecordRow, AuditLogRow, InterventionRow, PromiseRow, RuleChangeRow,
    SessionLocal, reset_database,
)


@pytest.fixture(autouse=True)
def _clean():
    reset_database()
    clock.reset()
    rules.reload()


# --- the loader swapped its source and nobody noticed -----------------------


def test_the_defaults_are_served_before_anything_is_seeded():
    """A fresh clone with no database still runs on the shipped table. The file
    is the default, not a bootstrap artefact."""
    assert rules.policy_for("FAILED_PAYMENT", "BANK_DOWNTIME") is not None
    assert rules.threshold("frequency_cap", "max_contacts") == 2


def test_an_edit_is_visible_to_the_next_read_with_no_restart():
    """Hot reload. The generation bump is the mechanism; this is the behaviour
    that matters."""
    assert rules.threshold("frequency_cap", "max_contacts") == 2
    admin.set_guardrail("frequency_cap", {"max_contacts": 3, "window_days": 7})
    assert rules.threshold("frequency_cap", "max_contacts") == 3


def test_editing_one_row_does_not_drop_the_others():
    """Stored rules OVERLAY the defaults. A merchant tuning one threshold must
    not silently lose every rule they did not touch."""
    admin.set_guardrail("frequency_cap", {"max_contacts": 3, "window_days": 7})
    assert rules.policy_for("OVERDUE_INVOICE", "PAYMENT_STALLED") is not None
    assert rules.threshold("quiet_hours", "start") == "20:00"


def test_reset_restores_every_shipped_value():
    admin.set_guardrail("frequency_cap", {"max_contacts": 5, "window_days": 3})
    admin.reset()
    assert rules.threshold("frequency_cap", "max_contacts") == 2
    assert rules.is_modified("guardrail", "frequency_cap") is False


def test_an_edited_row_is_marked_as_edited():
    """So the UI can show 'default → new' rather than presenting a changed value
    as though it had always said that."""
    assert rules.is_modified("guardrail", "value_ceiling") is False
    admin.set_guardrail("value_ceiling", {"requires_human_above": 7_500_000})
    assert rules.is_modified("guardrail", "value_ceiling") is True


# --- the validator is the whole risk surface --------------------------------


def test_the_shipped_defaults_pass_their_own_validator():
    """A validator that refuses the defaults is one nobody can use, and it would
    make `reset` a trap: restore, then be unable to save anything again."""
    for leak, table in rules.default_policies().items():
        for cause, row in table.items():
            validate_policy_row(leak, cause, row)
    for name, config in rules.default_guardrails().items():
        validate_guardrail_config(name, config)


@pytest.mark.parametrize("row, expected", [
    ({"strategy": "silent_retry", "schedule": ["20 minutes"],
      "rationale": "x"}, "schedule token"),
    ({"strategy": "do_whatever", "schedule": ["20m"],
      "rationale": "x"}, "unknown strategy"),
    ({"strategy": "silent_retry", "schedule": ["20m"], "max_attempts": 40,
      "rationale": "x"}, "ceiling"),
    ({"strategy": "silent_retry", "schedule": ["20m"], "max_attempt": 3,
      "rationale": "x"}, "unknown field"),
    ({"strategy": "silent_retry", "schedule": ["20m"]}, "rationale"),
    ({"strategy": "scheduled_retry", "schedule": ["+7d"],
      "notify_customer": True, "rationale": "x"}, "no channel"),
    ({"strategy": "scheduled_retry", "schedule": ["+7d"], "channel": "PIGEON",
      "rationale": "x"}, "unknown channel"),
])
def test_a_rule_that_would_break_the_batch_is_refused(row, expected):
    with pytest.raises(RuleInvalid) as exc:
        validate_policy_row("FAILED_PAYMENT", "BANK_DOWNTIME", row)
    assert any(expected in p for p in exc.value.problems), exc.value.problems


@pytest.mark.parametrize("name, config, expected", [
    ("frequency_cap", {"max_contacts": 500, "window_days": 7}, "range"),
    ("confidence_floor", {"minimum": 0.0}, "range"),
    ("quiet_hours", {"start": "25:00", "end": "09:00"}, "HH:MM"),
    ("value_ceiling", {"requires_human_above": 5_000_000,
                       "by_leak_type": {"NOT_A_TYPE": 1}}, "unknown leak type"),
    ("kill_switch", {"enabled": "yes please"}, "true or false"),
    ("frequency_cap", {"max_contats": 2}, "unknown key"),
])
def test_a_threshold_that_would_defeat_its_own_rule_is_refused(
    name, config, expected
):
    with pytest.raises(RuleInvalid) as exc:
        validate_guardrail_config(name, config)
    assert any(expected in p for p in exc.value.problems), exc.value.problems


def test_every_problem_is_reported_not_just_the_first():
    """Same reasoning as the guardrail engine collecting all violations: fixing
    one error to discover the next is a worse experience than seeing them all."""
    with pytest.raises(RuleInvalid) as exc:
        validate_policy_row("FAILED_PAYMENT", "BANK_DOWNTIME",
                            {"strategy": "nope", "schedule": ["soon"],
                             "max_attempts": 99})
    assert len(exc.value.problems) >= 3


def test_a_refused_edit_writes_nothing_at_all():
    """No partial application, and no entry in the change log for a change that
    did not happen."""
    before = rules.threshold("frequency_cap", "max_contacts")
    with pytest.raises(RuleInvalid):
        admin.set_guardrail("frequency_cap", {"max_contacts": 99,
                                              "window_days": 7})
    assert rules.threshold("frequency_cap", "max_contacts") == before
    assert admin.changes() == []


# --- the change log ---------------------------------------------------------


def test_every_accepted_edit_lands_in_the_change_log_with_a_before():
    admin.set_guardrail("value_ceiling", {"requires_human_above": 7_500_000},
                        actor="anita", note="judges asked")
    entry = admin.changes()[0]

    assert entry["scope"] == "guardrail"
    assert entry["key"] == "value_ceiling"
    assert entry["actor"] == "anita"
    assert entry["note"] == "judges asked"
    assert {"field": "requires_human_above", "before": 5_000_000,
            "after": 7_500_000} in entry["diff"]


def test_the_change_log_cannot_be_edited_or_erased():
    """The only answer to 'who widened the ceiling' is one nobody can rewrite.
    Enforced by the database, not by discipline."""
    admin.set_guardrail("frequency_cap", {"max_contacts": 3, "window_days": 7})

    with pytest.raises(Exception, match="append-only"):
        with SessionLocal() as session:
            session.query(RuleChangeRow).delete()
            session.commit()

    with pytest.raises(Exception, match="append-only"):
        with SessionLocal() as session:
            session.query(RuleChangeRow).update({"note": "tampered"})
            session.commit()


def test_a_reset_is_itself_logged():
    """It is the largest change anybody can make. Rules differing from yesterday
    with nothing saying why is the situation this log exists to prevent."""
    admin.set_guardrail("frequency_cap", {"max_contacts": 3, "window_days": 7})
    admin.reset()
    assert any(c["key"] == "*" for c in admin.changes())


# --- the what-if replay -----------------------------------------------------


def _seeded_arc():
    from reclaim.runner import run_batch, tick

    at = clock.now().replace(hour=11, minute=0, second=0, microsecond=0)
    run_batch(dry_run=True, frm=at)
    tick(advance="24h", dry_run=True)


def _counts():
    tables = (AtRiskRecordRow, AuditLogRow, InterventionRow, PromiseRow)
    with SessionLocal() as session:
        return tuple(session.query(t).count() for t in tables)


def test_a_replay_changes_nothing_in_the_live_database():
    """THE property. A what-if that wrote to the live database would corrupt the
    very numbers it exists to explain, and would do it invisibly — the diff comes
    back either way."""
    _seeded_arc()
    before, before_clock = _counts(), clock.offset().total_seconds()

    whatif.replay(whatif.parse_overrides(
        {"guardrails": {"value_ceiling": {"requires_human_above": 9_000_000}}}),
        arc=["24h"])

    assert _counts() == before
    assert abs(clock.offset().total_seconds() - before_clock) < 1.0


def test_a_replay_leaves_the_merchants_own_edits_in_place():
    """The override is layered on top of the CURRENT rules. Seeding the scratch
    database from the shipped file instead would discard every edit already made
    and attribute the difference to the one being tested."""
    _seeded_arc()
    admin.set_guardrail("cooldown", {"hours_between_contacts": 12})

    whatif.replay(whatif.parse_overrides(
        {"guardrails": {"frequency_cap": {"max_contacts": 4}}}), arc=["24h"])

    assert rules.threshold("cooldown", "hours_between_contacts") == 12


def test_the_replay_refuses_overrides_the_system_would_refuse_to_save():
    """A replay answering questions about a configuration nobody can have is
    worse than no replay."""
    _seeded_arc()
    with pytest.raises(RuleInvalid):
        whatif.parse_overrides(
            {"guardrails": {"frequency_cap": {"max_contacts": 99}}})


def test_a_replay_with_no_overrides_reports_no_difference():
    """Both sides are replayed, including the unchanged one. If they diverged
    with nothing changed, every reported delta would be noise."""
    _seeded_arc()
    diff = whatif.replay(whatif.Overrides(), arc=["24h"]).as_dict()
    assert diff["deltas"]["recovered_paise"] == 0
    assert diff["deltas"]["contacts"] == 0


def test_raising_the_ceiling_moves_work_off_the_human_queue():
    """The demo beat, asserted. A higher ceiling means fewer value_ceiling
    refusals — that is what the setting means."""
    _seeded_arc()
    diff = whatif.replay(whatif.parse_overrides(
        {"guardrails": {"value_ceiling": {"requires_human_above": 90_000_000}}}),
        arc=["24h", "48h"]).as_dict()

    moved = {g["guardrail"]: g["delta"] for g in diff["guardrails"]}
    assert moved.get("value_ceiling", 0) < 0


def test_replaying_with_nothing_to_replay_says_so():
    with pytest.raises(RuntimeError, match="Run a batch first"):
        whatif.replay(whatif.Overrides())


def test_the_frozen_diagnoses_come_from_the_audit_log():
    """Recomputing would re-run layer 2 and could produce different labels, at
    which point the replay compares two different batches and calls the
    difference a consequence of the rule change."""
    _seeded_arc()
    labels = whatif.frozen_diagnoses()
    assert labels

    with SessionLocal() as session:
        record_ids = {r.id for r in session.query(AtRiskRecordRow)}
    assert set(labels) <= record_ids
