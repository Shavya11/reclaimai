"""Request batching: many records, one call, and nothing shifted by one.

The arc's cost on a free tier is the number of REQUESTS it makes, so layer 2
carries ten unrelated records per call. That trade is only safe if three things
hold, and each one below is a way it could silently not:

- an answer lands on the record it is about, even when the model returns them
  out of order, drops one, or invents an index;
- a batch never mixes leak types, because the closed enum is narrowed per leak
  type and a widened one is a hallucination the policy table has no row for;
- a batch that fails costs correctness nothing - it degrades to the per-record
  path, and then to UNKNOWN, exactly as the unbatched layer always did.

None of this spends a token. The properties are ours, not the vendor's.
"""

from reclaim.brain.diagnosis.engine import diagnose_batch
from reclaim.brain.diagnosis.llm_diagnoser import (
    BATCH_SIZE, DIAGNOSIS_TOOL, CachedDiagnoser, _validate, batch_tool,
    batch_tool_for, tool_for, unpack_batch,
)
from reclaim.enums import LeakType, RootCause
from reclaim.models import AtRiskRecord, Diagnosis
from reclaim.timeutil import now


def _record(rid="R1", leak_type=LeakType.FAILED_PAYMENT, reason="payment_failed"):
    return AtRiskRecord(
        id=rid, leak_type=leak_type, amount=12400,
        counterparty_id=f"C{rid}", source_ref=f"pay_{rid}", detected_at=now(),
        raw_signals={
            "issuer_bank": "HDFC", "method": "card", "attempt_number": 1,
            "error": {"code": "BAD_REQUEST_ERROR", "reason": reason},
            "customer_history": {"same_instrument_succeeded_before": True},
        },
    )


def _diagnosis(cause=RootCause.INSUFFICIENT_FUNDS, reasoning="fake"):
    return Diagnosis(root_cause=cause, confidence=0.8, reasoning=reasoning,
                     recoverable=True, evidence_used=[], source="llm")


def _answer(index, reasoning):
    return {
        "index": index,
        "root_cause": RootCause.INSUFFICIENT_FUNDS.value,
        "confidence": 0.8,
        "reasoning": reasoning,
        "recoverable": True,
        "evidence_used": [],
    }


class Recorder(CachedDiagnoser):
    """A diagnoser that records how it was asked, and answers however told."""

    def __init__(self, batch=None, single=None, raises=None):
        super().__init__(client="fake", model="fake")
        self.batches: list[list] = []
        self.singles: list = []
        self._batch, self._single, self._raises = batch, single, raises

    def _signature(self, record, signal=None) -> str:
        return record.raw_signals["error"]["reason"]

    def _group(self, record, signal=None):
        return record.leak_type

    def _ask_batch(self, calls):
        self.batches.append([c[0].id for c in calls])
        if self._raises:
            raise self._raises
        return self._batch(calls) if self._batch else None

    def _ask(self, record, signal=None):
        self.singles.append(record.id)
        return self._single(record) if self._single else _diagnosis()


# --- ordering, the failure that would be invisible ----------------------------

def test_answers_land_on_the_record_they_are_about_when_returned_out_of_order():
    # The model answers correctly but in reverse. `index` is the only thing
    # standing between that and four labels shifted onto the wrong records.
    payload = {"answers": [_answer(i, f"about-{i}") for i in reversed(range(4))]}
    out = unpack_batch(payload, 4, _validate)
    assert [o.reasoning for o in out] == [f"about-{i}" for i in range(4)]


def test_an_index_out_of_range_is_dropped_not_wrapped_around():
    payload = {"answers": [_answer(0, "real"), _answer(9, "invented"),
                           _answer(-1, "negative")]}
    out = unpack_batch(payload, 2, _validate)
    assert out[0].reasoning == "real"
    assert out[1] is None


def test_a_duplicate_index_does_not_overwrite_the_first_answer():
    payload = {"answers": [_answer(0, "first"), _answer(0, "second")]}
    out = unpack_batch(payload, 2, _validate)
    assert out[0].reasoning == "first"
    assert out[1] is None


def test_a_malformed_entry_costs_its_own_slot_and_no_others():
    payload = {"answers": [_answer(0, "fine"), "not-a-dict",
                           {"index": 1, "root_cause": "NOT_A_CAUSE"}]}
    out = unpack_batch(payload, 2, _validate)
    assert out[0].reasoning == "fine"
    assert out[1] is None


def test_many_returns_answers_in_the_callers_order():
    records = [_record(f"R{i}", reason=f"r{i}") for i in range(3)]
    d = Recorder(batch=lambda calls: [_diagnosis(reasoning=c[0].id) for c in calls])
    out = d.many([(r, None) for r in records])
    assert [o.reasoning for o in out] == ["R0", "R1", "R2"]


# --- the narrowed enum --------------------------------------------------------

def test_the_batched_tool_narrows_its_enum_exactly_as_the_single_one_does():
    for leak_type in LeakType:
        record = _record(leak_type=leak_type)
        single = tool_for(record)["input_schema"]["properties"]["root_cause"]["enum"]
        answers = batch_tool_for(record)["input_schema"]["properties"]["answers"]
        assert answers["items"]["properties"]["root_cause"]["enum"] == single


def test_index_is_required_so_an_unlabelled_answer_cannot_be_accepted():
    item = batch_tool(DIAGNOSIS_TOOL)["input_schema"]["properties"]["answers"]["items"]
    assert "index" in item["required"]
    for field in DIAGNOSIS_TOOL["input_schema"]["required"]:
        assert field in item["required"]


def test_leak_types_never_share_a_request():
    records = ([_record(f"P{i}", LeakType.FAILED_PAYMENT, f"p{i}") for i in range(3)]
               + [_record(f"I{i}", LeakType.OVERDUE_INVOICE, f"i{i}") for i in range(3)])
    d = Recorder(batch=lambda calls: [_diagnosis() for _ in calls])
    d.many([(r, None) for r in records])

    for batch in d.batches:
        assert len({rid[0] for rid in batch}) == 1, f"mixed leak types: {batch}"


def test_a_group_larger_than_the_batch_size_is_cut_not_sent_whole():
    records = [_record(f"R{i}", reason=f"r{i}") for i in range(BATCH_SIZE + 3)]
    d = Recorder(batch=lambda calls: [_diagnosis() for _ in calls])
    d.many([(r, None) for r in records])
    # Sorted, because chunks are dispatched concurrently and finish in whatever
    # order the provider answers.
    assert sorted(len(b) for b in d.batches) == [3, BATCH_SIZE]


# --- deduplication ------------------------------------------------------------

def test_identical_records_are_asked_once_and_answered_everywhere():
    records = [_record(f"R{i}", reason="same") for i in range(5)]
    d = Recorder(batch=lambda calls: [_diagnosis(reasoning="once") for _ in calls],
                 single=lambda record: _diagnosis(reasoning="once"))
    out = d.many([(r, None) for r in records])

    # Collapsed to one question - and one question is asked singly, not through
    # the batched schema.
    assert d.batches == [], "duplicates must collapse before dispatch"
    assert d.singles == ["R0"]
    assert all(o.reasoning == "once" for o in out)
    assert d.cache_hits == 4


def test_a_second_call_is_answered_entirely_from_cache():
    records = [_record(f"R{i}", reason=f"r{i}") for i in range(3)]
    d = Recorder(batch=lambda calls: [_diagnosis() for _ in calls])
    d.many([(r, None) for r in records])
    d.batches.clear()

    out = d.many([(r, None) for r in records])
    assert d.batches == []
    assert all(o is not None for o in out)


# --- degradation --------------------------------------------------------------

def test_a_batch_that_raises_falls_back_to_asking_one_at_a_time():
    records = [_record(f"R{i}", reason=f"r{i}") for i in range(3)]
    d = Recorder(raises=RuntimeError("provider exploded"))
    out = d.many([(r, None) for r in records])

    assert sorted(d.singles) == ["R0", "R1", "R2"]
    assert all(o is not None for o in out)


def test_a_record_the_batch_skipped_gets_one_direct_question():
    records = [_record(f"R{i}", reason=f"r{i}") for i in range(3)]
    d = Recorder(batch=lambda calls: [_diagnosis() if i == 0 else None
                                      for i, _ in enumerate(calls)])
    out = d.many([(r, None) for r in records])

    assert sorted(d.singles) == ["R1", "R2"], "skipped records are re-asked singly"
    assert all(o is not None for o in out)


def test_many_never_raises_and_an_unanswerable_record_comes_back_none():
    records = [_record(f"R{i}", reason=f"r{i}") for i in range(2)]
    d = Recorder(batch=lambda calls: [None for _ in calls],
                 single=lambda record: None)
    assert d.many([(r, None) for r in records]) == [None, None]


def test_a_diagnoser_with_no_client_answers_nothing_rather_than_calling():
    d = Recorder(batch=lambda calls: [_diagnosis() for _ in calls])
    d._client = None
    assert d.many([(_record(), None)]) == [None]
    assert d.batches == []


# --- the engine seam ----------------------------------------------------------

def test_the_engine_batches_a_diagnoser_that_offers_it():
    records = [_record(f"R{i}", reason=f"unresolvable-{i}") for i in range(4)]
    d = Recorder(batch=lambda calls: [_diagnosis() for _ in calls])
    out, _ = diagnose_batch(records, {}, llm=d)

    assert len(d.batches) == 1, "four unresolved records must be one request"
    assert all(out[r.id].source == "llm" for r in records)


def test_the_engine_still_works_with_a_bare_callable():
    """`--no-llm` and every test double inject a plain function. The batched
    path must be an offer, never a requirement."""
    records = [_record(f"R{i}", reason=f"unresolvable-{i}") for i in range(3)]
    seen = []

    def llm(record, signal=None):
        seen.append(record.id)
        return _diagnosis()

    out, _ = diagnose_batch(records, {}, llm=llm)
    assert seen == ["R0", "R1", "R2"]
    assert all(out[r.id].source == "llm" for r in records)


def test_records_layer_1_resolves_are_never_sent_to_layer_2():
    resolved = _record("R0", reason="card_expired")
    unresolved = _record("R1", reason="unresolvable")
    d = Recorder(batch=lambda calls: [_diagnosis() for _ in calls])
    out, _ = diagnose_batch([resolved, unresolved], {}, llm=d)

    assert d.batches == [], "one survivor is one ordinary question"
    assert d.singles == ["R1"], "only the record layer 1 could not resolve"
    assert out["R0"].source != "llm"


def test_an_llm_that_answers_nothing_leaves_unknowns_not_holes():
    records = [_record(f"R{i}", reason=f"unresolvable-{i}") for i in range(3)]
    d = Recorder(batch=lambda calls: [None for _ in calls], single=lambda r: None)
    out, _ = diagnose_batch(records, {}, llm=d)

    assert set(out) == {r.id for r in records}
    assert all(out[r.id].root_cause is RootCause.UNKNOWN for r in records)


def test_a_record_the_per_item_fallback_declines_is_not_asked_twice():
    """A batch that raises drops to the per-item path. If that path also answers
    None, the record is UNKNOWN - asking it again spends a second call to be
    told the same thing, on the tier least able to afford it."""
    records = [_record(f"R{i}", reason=f"r{i}") for i in range(2)]
    d = Recorder(raises=RuntimeError("provider exploded"),
                 single=lambda record: None)
    assert d.many([(r, None) for r in records]) == [None, None]
    assert sorted(d.singles) == ["R0", "R1"]


def test_chunks_go_out_concurrently_up_to_the_cap():
    """MAX_CONCURRENCY sat unused for the life of this file: the caller asked
    record by record, in a loop, so nothing ever contended for the semaphore.
    A provider that paces itself still serialises behind its own gate - the cap
    is a ceiling, not a target - but the ceiling has to be real."""
    import threading

    from reclaim.brain.diagnosis.llm_diagnoser import MAX_CONCURRENCY

    started = threading.Barrier(3, timeout=5)

    def concurrent(calls):
        started.wait()  # only returns if three chunks are in flight together
        return [_diagnosis() for _ in calls]

    records = [_record(f"R{i}", reason=f"r{i}") for i in range(3 * BATCH_SIZE)]
    d = Recorder(batch=concurrent)
    out = d.many([(r, None) for r in records])

    assert len(d.batches) == 3
    assert all(o is not None for o in out)
    assert MAX_CONCURRENCY >= 3


def test_a_single_record_is_asked_the_way_it_always_was():
    """The sandbox previews one record through `diagnose_batch`. One record is
    the same one request either way, so it must keep the single-item tool and
    the prompt that goes with it rather than being told it is reading a list."""
    d = Recorder(batch=lambda calls: [_diagnosis() for _ in calls])
    out = d.many([(_record("SBX", reason="free text a visitor typed"), None)])

    assert d.batches == [], "one record must not use the batched schema"
    assert d.singles == ["SBX"]
    assert out[0] is not None


def test_the_ablation_stays_on_the_per_record_path():
    """`evidence/ablation.json`, the README row and DAY7-HANDOFF all publish
    38 layer-2 API calls, measured record by record. The ablation's wrapper does
    not forward `many`, so batching cannot move that number behind their backs -
    and this is the test that says so out loud rather than leaving it to luck."""
    from reclaim.experiments.ablation import _CountingDiagnoser

    counted = _CountingDiagnoser(Recorder(batch=lambda c: [_diagnosis() for _ in c]))
    assert not hasattr(counted, "many"), (
        "forwarding many() rebatches the ablation and moves a published figure"
    )

    records = [_record(f"R{i}", reason=f"unresolvable-{i}") for i in range(4)]
    diagnose_batch(records, {}, llm=counted)
    assert counted._inner.batches == [], "the ablation must not batch"
    assert sorted(counted._inner.singles) == ["R0", "R1", "R2", "R3"]
