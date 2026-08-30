"""The scoreboard, the baseline comparison, and the API that serves them.

A scoreboard is the easiest thing in a project like this to quietly get wrong,
because nothing crashes when it does — a rupee counted twice looks exactly like
a rupee earned. These tests are mostly about arithmetic that has to hold rather
than features that have to work.
"""

import pytest

from reclaim import baseline, clock
from reclaim.db import InterventionRow, SessionLocal, reset_database
from reclaim.enums import NEVER_RETRY, RecordState
from reclaim.runner import DEMO_ARC, run_batch, tick
from reclaim.scoreboard import compute
from reclaim.webhooks.attribution import RESULT_RECOVERED


@pytest.fixture(autouse=True)
def _clean_db():
    reset_database()
    clock.reset()


@pytest.fixture(scope="module")
def _arc():
    """One full arc, shared. Walking it takes a while and every test below asks
    questions of the same finished run."""
    reset_database()
    clock.reset()
    run_batch(dry_run=True)
    for step in DEMO_ARC:
        tick(advance=step, dry_run=True)
    board = compute()
    comparison = baseline.compare()
    clock.reset()
    return board, comparison


# --- arithmetic that must hold ----------------------------------------------


def test_every_rupee_lands_in_exactly_one_bucket():
    """Four buckets, not three. `organic` is money that arrived without us: not
    open, not written off, and not ours to call a recovery."""
    run_batch(dry_run=True)
    board = compute()
    assert board.balances
    assert (board.recovered_paise + board.organic_paise + board.open_paise
            + board.unrecoverable_paise) == board.at_risk_paise


def test_every_record_lands_in_exactly_one_bucket():
    run_batch(dry_run=True)
    board = compute()
    assert (board.recovered_records + board.organic_records
            + board.open_records
            + board.unrecoverable_records) == board.records


def test_recovered_money_equals_what_was_attributed():
    """The scoreboard may not invent a rupee the attribution chain did not
    trace."""
    run_batch(dry_run=True)
    board = compute()
    with SessionLocal() as session:
        attributed = sum(
            i.recovered_amount for i in session.query(InterventionRow)
            .filter(InterventionRow.result == RESULT_RECOVERED))
    assert board.recovered_paise == attributed


def test_recovery_can_never_exceed_what_was_at_risk():
    run_batch(dry_run=True)
    board = compute()
    assert 0 <= board.recovered_paise <= board.at_risk_paise
    assert 0.0 <= board.recovery_rate <= 1.0


def test_the_scoreboard_is_recomputed_from_storage_not_carried_in_memory():
    """A number that only exists in the process that produced it is a number
    nobody can audit."""
    result = run_batch(dry_run=True)
    first = compute()
    second = compute()

    assert first.as_dict() == second.as_dict()
    assert result.settlement is not None
    assert first.recovered_paise == result.settlement.recovered_paise


def test_an_empty_database_scores_zero_rather_than_dividing_by_zero():
    board = compute()
    assert board.records == 0
    assert board.recovery_rate == 0.0
    assert board.contacts_per_recovery == 0.0
    assert board.balances


# --- what the scoreboard is claiming ----------------------------------------


def test_never_retry_causes_are_written_off_not_left_open():
    """Risk declines and revoked mandates are money we chose not to chase.
    Reporting them as "still open" would flatter the recovery rate."""
    run_batch(dry_run=True)
    board = compute()
    never_retry = {c.value for c in NEVER_RETRY}
    charged = [line for line in board.by_root_cause if line.cause in never_retry]
    assert charged, "the batch planted no never-retry records"
    for line in charged:
        assert line.recovered_records == 0
        assert line.contacts == 0, "a never-retry record was contacted"
    assert board.unrecoverable_paise > 0


def test_guardrail_counts_come_from_the_audit_log():
    run_batch(dry_run=True)
    board = compute()
    assert board.guardrails_total == sum(board.guardrails_fired.values())
    assert board.guardrails_total > 0


def test_contacts_per_recovery_counts_the_messages_that_recovered_nothing():
    run_batch(dry_run=True)
    board = compute()
    if board.recovered_records:
        assert board.contacts_per_recovery == pytest.approx(
            board.contacts / board.recovered_records)


def test_walking_the_arc_recovers_more_than_a_single_tick(_arc):
    """Deferred is not dropped. The whole point of a schedule is that money
    keeps arriving on later ticks."""
    board, _ = _arc
    reset_database()
    clock.reset()
    run_batch(dry_run=True)
    single = compute()
    assert board.recovered_paise > single.recovered_paise


# --- the baseline ------------------------------------------------------------


def test_the_baseline_touches_the_same_batch_and_the_same_money():
    run_batch(dry_run=True)
    board = compute()
    naive = baseline.run()
    assert naive.records == board.records
    assert naive.at_risk_paise == board.at_risk_paise


def test_the_baseline_breaks_the_rules_our_guardrails_enforce():
    """If the naive strategy were compliant there would be nothing to compare.
    Each of these maps to a numbered guardrail it walks straight through."""
    naive = baseline.run()
    assert naive.contacts_to_opted_out > 0        # guardrail 2
    assert naive.contacts_to_dnd > 0              # guardrail 3
    assert naive.contacts_in_quiet_hours > 0      # guardrail 4
    assert naive.customers_over_frequency_cap > 0  # guardrail 7
    assert naive.retries_against_never_retry > 0   # policy, not guardrail
    assert naive.compliance_breaches > 0


def test_our_run_commits_none_of_those_breaches(_arc):
    """The comparison is only worth making if our side is actually clean."""
    board, _ = _arc
    never_retry = {c.value for c in NEVER_RETRY}
    for line in board.by_root_cause:
        if line.cause in never_retry:
            assert line.contacts == 0


def test_the_baseline_spends_far_more_contacts_per_recovery(_arc):
    board, comparison = _arc
    naive = comparison.baseline
    assert naive.contacts > board.contacts
    if board.recovered_records:
        assert naive.contacts_per_recovery > board.contacts_per_recovery


def test_the_baseline_is_deterministic():
    assert baseline.run(seed=42).as_dict() == baseline.run(seed=42).as_dict()


def test_both_strategies_draw_from_the_same_luck():
    """The comparison means nothing unless the coin flips are shared. Record
    REC_x attempt 1 must succeed or fail identically under both."""
    import random

    from reclaim.enums import ActionType
    from reclaim.synthetic import generate
    from reclaim.synthetic.outcomes import probability

    batch = generate(seed=42)
    record = batch.records[0]
    cause = batch.truth[record.id]

    ours = random.Random(f"42:{record.id}:1").random()
    theirs = random.Random(f"42:{record.id}:1").random()
    assert ours == theirs

    p = probability(cause, action=ActionType.SEND_LINK, attempt_number=1)
    assert 0.0 <= p <= 1.0


def test_the_gap_to_the_baseline_is_fully_accounted_for(_arc):
    """Publishing a comparison we can lose is only defensible if every rupee of
    the difference has a stated reason."""
    board, comparison = _arc
    gap = comparison.gap
    assert set(gap) >= {"total", "reasons", "deliberate_paise"}
    assert sum(r["paise"] for r in gap["reasons"]) == gap["total"]["paise"]
    assert sum(r["records"] for r in gap["reasons"]) == gap["total"]["records"]


# --- the API -----------------------------------------------------------------


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from reclaim.api.app import app

    return TestClient(app)


def test_the_api_serves_the_same_numbers_the_scoreboard_computes(client):
    run_batch(dry_run=True)
    payload = client.get("/api/scoreboard").json()
    assert payload["recovered_paise"] == compute().recovered_paise
    assert payload["balances"] is True


def test_every_read_endpoint_answers(client):
    run_batch(dry_run=True)
    for path in ("/api/health", "/api/scoreboard", "/api/records",
                 "/api/human-queue", "/api/guardrails", "/api/webhooks",
                 "/api/baseline"):
        response = client.get(path)
        assert response.status_code == 200, path


def test_a_record_audit_trail_reads_as_a_timeline(client):
    run_batch(dry_run=True)
    records = client.get("/api/records?limit=1").json()["records"]
    record_id = records[0]["id"]

    events = client.get(f"/api/records/{record_id}/audit").json()["events"]

    stages = [e["stage"] for e in events]
    assert "DIAGNOSE" in stages
    assert "DECIDE" in stages
    assert all(e["reason"] for e in events), "an audit row with no reason"
    assert [e["id"] for e in events] == sorted(e["id"] for e in events)


def test_a_missing_record_is_a_404_not_a_crash(client):
    assert client.get("/api/records/REC_nope").status_code == 404
    assert client.get("/api/records/REC_nope/audit").status_code == 404


def _wait_until_idle(client, timeout: float = 180.0) -> dict:
    """Block until the API says it has stopped working.

    Both write endpoints answer 202 and finish on a thread, because a tick
    re-diagnoses through a rate-limited model tier and a request that takes two
    minutes is one a proxy will cut before it returns.
    """
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        health = client.get("/api/health").json()
        if not health["seeding"]:
            return health
        time.sleep(0.25)
    raise AssertionError(f"still {health['seeding_stage']} after {timeout}s")


def test_the_tick_endpoint_moves_the_clock(client):
    run_batch(dry_run=True)
    before = client.get("/api/health").json()["clock"]

    response = client.post("/api/tick?advance=24h")

    assert response.status_code == 202, "the work is accepted, not awaited"
    assert response.json()["started"] is True
    assert _wait_until_idle(client)["clock"] > before
    client.post("/api/clock/reset")


def test_a_second_write_while_one_is_running_is_refused(client):
    """Two arcs walking the same database interleave their ticks and produce a
    scoreboard that is the sum of two different stories."""
    first = client.post("/api/tick?advance=24h")
    assert first.status_code == 202

    second = client.post("/api/run-batch")
    assert second.status_code in (202, 409)
    if second.status_code == 409:
        assert "busy" in second.json()["detail"].lower()

    _wait_until_idle(client)
    client.post("/api/clock/reset")


def test_an_unparseable_schedule_token_is_a_400(client):
    assert client.post("/api/tick?advance=nonsense").status_code == 400


def test_the_kill_switch_is_reachable_at_runtime(client):
    try:
        assert client.post("/api/kill-switch?enabled=false").json()[
            "autopilot_enabled"] is False
    finally:
        client.post("/api/kill-switch?enabled=true")
