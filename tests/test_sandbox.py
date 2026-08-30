"""The sandbox: a visitor's submission, and what it is allowed to touch.

Two properties carry this feature, and both are here because neither is obvious
from reading the code that provides them.

**Preview writes nothing.** It runs the real diagnosers, the real policy table
and the real gate, and the only thing separating it from a batch is that it
stops before executing. A preview that quietly wrote an audit row would corrupt
the trail it exists to explain, and it would do so invisibly, because the trace
comes back looking identical either way.

**A committed record moves no published figure.** The scoreboard is the one
number computed by asking the live database what is in it, so a stranger typing
into the demo is the one way an outsider can move `contacts_per_recovery` or the
headline. `verify` checks this too; it is here as well because a test names the
regression and a verify check names the property.
"""

from datetime import timedelta

import pytest

from reclaim import clock, sandbox
from reclaim.db import (
    AtRiskRecordRow, AuditLogRow, ExecutedActionRow, InterventionRow,
    SessionLocal, reset_database,
)
from reclaim.brain.conversation.intent import keyword_reading
from reclaim.enums import LeakType
from reclaim.provenance import USER_PREFIX, is_user_record
from reclaim.runner import run_batch
from reclaim.scoreboard import compute


@pytest.fixture(autouse=True)
def _clean_db():
    reset_database()
    clock.reset()


def _seeded_batch():
    """A real batch, so the gate has contact history and customers to judge."""
    return run_batch(seed=42, dry_run=True, settle=False, llm=None,
                     extractor=None)


def _counts() -> tuple[int, ...]:
    with SessionLocal() as session:
        return tuple(session.query(t).count() for t in
                     (AtRiskRecordRow, AuditLogRow, InterventionRow,
                      ExecutedActionRow))


def _submission(**kw) -> sandbox.Submission:
    base = {"error_reason": "card_expired", "error_code": "BAD_REQUEST_ERROR",
            "text": "Your card has expired.", "amount_paise": 249_900}
    return sandbox.Submission(**{**base, **kw})


# --- preview writes nothing ---------------------------------------------------


def test_preview_writes_nothing_at_all():
    _seeded_batch()
    before = _counts()
    for preset in sandbox.PRESETS:
        sandbox.preview(sandbox.Submission(**preset["submission"]))
    assert _counts() == before


def test_preview_does_not_move_the_demo_clock():
    _seeded_batch()
    before = clock.offset().total_seconds()
    sandbox.preview(_submission())
    assert clock.offset().total_seconds() == before


def test_preview_produces_a_trace_every_stage_of_which_names_its_decider():
    _seeded_batch()
    trace = sandbox.preview(_submission())
    assert trace["trace"], "a preview with no stages renders an empty strip"
    allowed = {"detector", "model", "table", "gate", "runner"}
    for stage in trace["trace"]:
        assert stage["decided_by"] in allowed, stage


def test_a_layer_one_hit_never_carries_the_model_badge():
    """CLAUDE.md's one rule, as something the strip can be checked against.

    `card_expired` is in the deterministic map, so layer 2 is never consulted
    and no card may claim the model decided anything.
    """
    _seeded_batch()
    trace = sandbox.preview(_submission())
    assert not [s for s in trace["trace"] if s["decided_by"] == "model"]


def test_an_unmapped_error_falls_through_and_says_so():
    _seeded_batch()
    trace = sandbox.preview(_submission(error_reason="", text="It just failed."))
    stages = {s["stage"]: s for s in trace["trace"]}
    assert stages["DIAGNOSE L1"]["output"] == "NO MATCH"
    assert "DIAGNOSE L2" in stages


def test_without_model_still_completes_and_reaches_a_human():
    """Rule 7: the batch always completes. With layer 2 refused and layer 1
    missing, the record must reach UNKNOWN and a person — not an exception, and
    not a confident-looking guess."""
    _seeded_batch()
    trace = sandbox.preview(
        _submission(error_reason="", text="No idea.", without_model=True))
    assert trace["verdict"] in {"HUMAN", "BLOCKED", "SCHEDULED"}


# --- commit is real, and stays in its own bucket ------------------------------


def test_commit_creates_a_user_record_that_ran_through_the_real_runner():
    _seeded_batch()
    before = _counts()
    result = sandbox.commit(_submission())

    assert is_user_record(result["record_id"])
    assert result["committed"] is True
    after = _counts()
    assert after[0] == before[0] + 1, "the record itself was not stored"
    assert after[1] > before[1], "the runner wrote no audit rows"
    assert result["trace"], "no trace could be read back off the audit log"


def test_a_committed_record_moves_no_published_figure():
    _seeded_batch()
    before = compute().as_dict()
    sandbox.commit(_submission(amount_paise=9_900_000))
    after = compute().as_dict()

    moved = [k for k, v in before.items()
             if not k.startswith("user_") and after.get(k) != v]
    assert moved == [], f"a visitor record moved {moved}"
    assert after["user_records"] == 1
    assert after["user_at_risk_paise"] == 9_900_000


def test_two_commits_get_two_ids_and_never_reuse_one():
    """`audit_log` is append-only. A reused id grafts a new submission onto an
    older record's history, and nothing downstream could tell them apart."""
    _seeded_batch()
    first = sandbox.commit(_submission())["record_id"]
    second = sandbox.commit(_submission())["record_id"]
    assert first != second
    assert all(r.startswith(USER_PREFIX) for r in (first, second))


def test_committing_the_same_submission_twice_executes_no_key_twice():
    """The idempotency guarantee, over the one surface a stranger can drive."""
    _seeded_batch()
    sandbox.commit(_submission())
    sandbox.commit(_submission())
    with SessionLocal() as session:
        keys = [k for (k,) in session.query(ExecutedActionRow.idempotency_key).all()]
    assert len(keys) == len(set(keys)), "an idempotency key executed twice"


def test_a_committed_record_is_visible_where_a_visitor_would_look():
    """The point of committing rather than previewing: it shows up."""
    _seeded_batch()
    record_id = sandbox.commit(_submission())["record_id"]
    with SessionLocal() as session:
        assert session.get(AtRiskRecordRow, record_id) is not None
        assert (session.query(AuditLogRow)
                .filter(AuditLogRow.record_id == record_id).count()) > 0


def test_commit_does_not_re_propose_the_seeded_batch():
    """`only=` is what keeps a demo button from running 180 records. Without it
    every submission would re-propose the whole batch and bury the guardrail
    counters under the same refusals again."""
    _seeded_batch()
    result = sandbox.commit(_submission())
    assert result["batch"]["proposed"] <= 1


def test_an_abandoned_cart_submission_is_a_different_leak_type_not_a_special_case():
    _seeded_batch()
    trace = sandbox.preview(
        sandbox.Submission(leak_type=LeakType.ABANDONED_CART,
                           text="Checkout never completed."))
    assert trace["trace"][0]["output"] == LeakType.ABANDONED_CART.value


def test_a_submission_above_every_ceiling_is_refused_rather_than_executed():
    _seeded_batch()
    trace = sandbox.preview(_submission(amount_paise=10_000_000_00))
    assert trace["verdict"] != "ALLOWED"


# --- the record is a record, not a special kind of one ------------------------


def test_a_committed_record_obeys_the_frequency_cap_like_any_other():
    """Guardrail 7 is customer-level, so a submission attached to a customer who
    has already been contacted twice this week must be refused. A record whose
    customer has no history could never be blocked, which would make the demo
    look safer than the system is."""
    _seeded_batch()
    customer = sandbox.DEFAULT_CUSTOMER
    for _ in range(4):
        sandbox.commit(_submission(customer_id=customer))

    with SessionLocal() as session:
        contacts = (session.query(InterventionRow)
                    .filter(InterventionRow.outcome == "EXECUTED")
                    .filter(InterventionRow.channel.isnot(None))
                    .filter(InterventionRow.record_id.like(f"{USER_PREFIX}%"))
                    .count())
    assert contacts <= 2, f"{contacts} contacts to one customer in a week"


def test_the_record_id_space_survives_a_deleted_row():
    """Ids come off the highest stored id, not a row count."""
    _seeded_batch()
    first = sandbox.commit(_submission())["record_id"]
    with SessionLocal() as session:
        session.delete(session.get(AtRiskRecordRow, first))
        session.commit()
    second = sandbox.commit(_submission())["record_id"]
    assert second != first


def test_detected_at_is_timezone_aware_ist():
    record = sandbox.build_record(_submission(), "USR_9000")
    assert record.detected_at.tzinfo is not None
    assert record.detected_at <= clock.now() + timedelta(seconds=1)


# --- the reply reader, and the three bugs that hid behind each other ----------


def test_the_extractor_is_called_the_way_it_is_actually_shaped():
    """`CachedDiagnoser.__call__(*args)` takes POSITIONAL arguments and has no
    `read()` method.

    The first version of `read_reply` called `extractor.read(reply, today=...)`
    inside a broad `except Exception`, so the AttributeError was swallowed and
    every reply came back "No model available and no recognisable phrase" —
    while a working model sat right there. A false sentence about whether the
    model was consulted is the one thing that screen must never print.
    """
    seen = {}

    class Extractor:
        available = True

        def __call__(self, *args):
            seen["args"] = args
            return keyword_reading("I will pay on Friday")

        def read(self, *a, **k):  # noqa: D401 - must never be preferred
            raise AssertionError("read() is not the extractor's interface")

    import reclaim.sandbox as sbx

    original = sbx._build_extractor
    sbx._build_extractor = lambda: Extractor()
    try:
        sbx.read_reply("I will pay on Friday")
    finally:
        sbx._build_extractor = original

    assert "args" in seen, "the extractor was never called"
    assert seen["args"][0] == "I will pay on Friday"


def test_free_text_submissions_do_not_share_one_cache_key():
    """The signature ignored `description`, which is the only field a free-text
    submission varies. Three unrelated sentences hashed identically, so a warm
    cache would have answered the second with the first one's diagnosis —
    confidently, and invisibly."""
    from reclaim.brain.diagnosis.llm_diagnoser import signature

    texts = ["Card has expired, customer needs a new one",
             "Customer disputes this charge entirely",
             "Bank was down for an hour last night"]
    keys = {signature(sandbox.build_record(sandbox.Submission(text=t), "USR_x"))
            for t in texts}
    assert len(keys) == 3, "different submissions collapsed to one cache key"


def test_the_seeded_batch_keeps_its_cache_grouping():
    """The fix above must not split the seeded batch. AMBIGUOUS records share a
    reason and vary their wording, so keying on description unconditionally
    would have changed the API-call count the ablation publishes."""
    from collections import defaultdict

    from reclaim.brain.diagnosis.llm_diagnoser import signature
    from reclaim.synthetic import generate

    groups = defaultdict(set)
    for record in generate(seed=42).records:
        groups[signature(record)].add(record.id)
    # Every seeded record carries a reason code, so none of them can be keyed on
    # description — which is what keeps the grouping identical.
    for record in generate(seed=42).records:
        error = record.raw_signals.get("error") or {}
        if record.leak_type is not LeakType.ABANDONED_CART and error:
            assert error.get("reason"), f"{record.id} has no reason code"


def test_the_sandbox_diagnoser_is_shared_so_the_cache_survives():
    """`api.app._llm()` builds a new diagnoser per call and the cache lives on
    the instance, so the sandbox never had one. Every preview was a live API
    call, and identical input could come back diagnosed differently."""
    assert sandbox._shared_llm() is sandbox._shared_llm()
