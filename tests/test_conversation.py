"""The conversation layer: what the model may read, and what it may not decide.

The point under test is the same one the diagnosis layer makes. The model turns
a sentence into a label from a closed set. A deterministic table turns that label
into an effect. Nothing the model returns — including a date it read correctly —
reaches a record without passing validation that the model has no part in.
"""

from datetime import timedelta

import pytest

from reclaim import clock, promises
from reclaim.brain.conversation import EFFECTS, apply_reading, process_replies
from reclaim.brain.conversation.intent import (
    INTENT_TOOL, IntentExtractor, _validate, keyword_reading,
)
from reclaim.db import (
    AtRiskRecordRow, CustomerRow, HumanQueueRow, PromiseRow, SessionLocal,
    reset_database,
)
from reclaim.enums import LeakType, RecordState, ReplyIntent, Stage
from reclaim.models import AtRiskRecord, ReplyReading
from reclaim.repository import save_batch


@pytest.fixture(autouse=True)
def _clean():
    reset_database()
    clock.reset()


def _now():
    return clock.now().replace(hour=11, minute=0, second=0, microsecond=0)


def _record(rid="INV_7000"):
    return AtRiskRecord(
        id=rid, leak_type=LeakType.OVERDUE_INVOICE, amount=250_000_00,
        counterparty_id="BUYER_9000", source_ref="inv_1",
        detected_at=_now() - timedelta(days=30),
        due_at=_now() - timedelta(days=30), raw_signals={"days_overdue": 30},
    )


class _Customer:
    def __init__(self, cid="BUYER_9000"):
        self.id, self.email, self.phone = cid, "ap@x.example.com", "+918000000000"
        self.opted_out = self.on_dnd = False
        self.successful_payments_lifetime = 12
        self.last_successful_at = None


def _store(record):
    save_batch([record], [_Customer(record.counterparty_id)])
    return record


def _reading(intent, confidence=0.9, promised_date=None):
    return ReplyReading(intent=intent, confidence=confidence, reasoning="t",
                        quote="q", promised_date=promised_date)


# --- the closed set ---------------------------------------------------------


def test_the_tool_offers_exactly_the_closed_intent_set():
    """A hallucination is harmless only while the model cannot invent an intent
    the effects table has no row for."""
    offered = set(INTENT_TOOL["input_schema"]["properties"]["intent"]["enum"])
    assert offered == {i.value for i in ReplyIntent}


def test_every_intent_has_an_effect():
    """A label with no row would fall through to whatever the last branch does,
    silently. Every member, or the table is not a table."""
    assert set(EFFECTS) == set(ReplyIntent)


def test_a_payload_outside_the_schema_becomes_nothing_not_a_guess():
    for bad in ({"intent": "PAY_LATER_MAYBE", "confidence": 0.9,
                 "reasoning": "r", "quote": "q"},
                {"intent": "PROMISE_TO_PAY", "confidence": "very",
                 "reasoning": "r", "quote": "q"},
                {"confidence": 0.9, "reasoning": "r", "quote": "q"}):
        assert _validate(bad) is None


def test_the_extractor_never_raises_when_the_provider_fails():
    """A dead model degrades the reply to unread. It does not stop the batch."""

    class Exploding:
        class messages:
            @staticmethod
            def create(**_):
                raise RuntimeError("provider down")

    assert IntentExtractor(client=Exploding(), model="m")("anything") is None


def test_replies_are_cached_by_their_words():
    """Two buyers writing the same sentence are one call. This is what makes
    the conversation layer affordable on a free tier."""
    calls = []

    class Fake:
        class messages:
            @staticmethod
            def create(**kw):
                calls.append(kw)

                class Block:
                    type = "tool_use"
                    input = {"intent": "PROMISE_TO_PAY", "confidence": 0.9,
                             "reasoning": "r", "quote": "q"}

                return type("R", (), {"content": [Block()]})()

    extractor = IntentExtractor(client=Fake(), model="m")
    for _ in range(4):
        extractor("friday tak ho jayega", _now())
    assert len(calls) == 1
    assert extractor.cache_hits == 3


# --- the deterministic effects ----------------------------------------------


def test_a_promise_with_a_valid_date_puts_the_record_to_sleep():
    record = _store(_record())
    when = (_now() + timedelta(days=6)).strftime("%Y-%m-%d")

    outcome = apply_reading(record,
                            _reading(ReplyIntent.PROMISE_TO_PAY,
                                     promised_date=when),
                            "friday tak ho jayega", at=_now())

    assert outcome == "PROMISED"
    assert len(promises.open_promises()) == 1
    with SessionLocal() as session:
        assert session.get(AtRiskRecordRow, record.id).state == \
            RecordState.PROMISED.value


def test_a_promise_whose_date_fails_validation_reaches_a_human():
    """THE test for the rule about time. The model read a date; the system will
    not act on it; nobody invents a substitute."""
    record = _store(_record())
    far = (_now() + timedelta(days=300)).strftime("%Y-%m-%d")

    outcome = apply_reading(record,
                            _reading(ReplyIntent.PROMISE_TO_PAY,
                                     promised_date=far),
                            "we'll pay next year", at=_now())

    assert outcome == "PROMISE_REJECTED"
    assert promises.open_promises() == {}
    with SessionLocal() as session:
        assert session.query(HumanQueueRow).count() == 1


def test_a_promise_with_no_date_is_not_turned_into_one():
    record = _store(_record())
    outcome = apply_reading(record, _reading(ReplyIntent.PROMISE_TO_PAY),
                            "will pay when I can", at=_now())
    assert outcome == "PROMISE_REJECTED"
    assert promises.open_promises() == {}


def test_stop_contacting_opts_the_customer_out_permanently():
    """No new machinery: it sets the flag the V1 consent guardrail already
    reads, which then closes every record that customer owns."""
    record = _store(_record())
    outcome = apply_reading(record, _reading(ReplyIntent.STOP_CONTACTING),
                            "mat bhejo, band karo", at=_now())

    assert outcome == "OPTED_OUT"
    with SessionLocal() as session:
        assert session.get(CustomerRow, record.counterparty_id).opted_out is True


def test_stop_contacting_is_honoured_even_below_the_confidence_floor():
    """Asymmetric on purpose. Wrongly staying silent costs one unsent message;
    wrongly continuing is a compliance breach."""
    record = _store(_record())
    outcome = apply_reading(record,
                            _reading(ReplyIntent.STOP_CONTACTING,
                                     confidence=0.2),
                            "stop", at=_now())
    assert outcome == "OPTED_OUT"


def test_a_claim_of_payment_is_reconciled_not_believed():
    record = _store(_record())
    outcome = apply_reading(record, _reading(ReplyIntent.ALREADY_PAID),
                            "payment kar diya, UTR 8829", at=_now())

    assert outcome == "CLAIMS_PAID"
    with SessionLocal() as session:
        state = session.get(AtRiskRecordRow, record.id).state
    assert state != RecordState.RECOVERED.value, \
        "a customer saying they paid is not a recovered rupee"


@pytest.mark.parametrize("intent", [
    ReplyIntent.DISPUTED,
    ReplyIntent.PARTIAL_PAYMENT_OFFER,
    ReplyIntent.WRONG_CONTACT,
    ReplyIntent.UNCLEAR,
])
def test_the_commercial_and_ambiguous_intents_reach_a_person(intent):
    record = _store(_record())
    apply_reading(record, _reading(intent, confidence=0.95), "text", at=_now())
    with SessionLocal() as session:
        assert session.query(HumanQueueRow).count() == 1


def test_a_low_confidence_reading_is_treated_as_not_understood():
    record = _store(_record())
    when = (_now() + timedelta(days=6)).strftime("%Y-%m-%d")
    outcome = apply_reading(record,
                            _reading(ReplyIntent.PROMISE_TO_PAY,
                                     confidence=0.3, promised_date=when),
                            "maybe friday?", at=_now())

    assert outcome == "LOW_CONFIDENCE"
    assert promises.open_promises() == {}, \
        "a half-understood sentence must not silence the agent for a week"


# --- the batch path ---------------------------------------------------------


def test_every_reply_is_audited_with_the_words_that_produced_it():
    """The audit trail has to carry the sentence, the label and the confidence,
    or 'why did it go quiet' has no answer."""
    from reclaim.db import AuditLogRow

    record = _store(_record())
    when = (_now() + timedelta(days=6)).strftime("%Y-%m-%d")
    apply_reading(record, _reading(ReplyIntent.PROMISE_TO_PAY,
                                   promised_date=when),
                  "sir friday tak ho jayega", at=_now())

    with SessionLocal() as session:
        row = (session.query(AuditLogRow)
               .filter(AuditLogRow.stage == Stage.REPLY.value).one())
    assert row.payload["reply_text"] == "sir friday tak ho jayega"
    assert row.payload["intent"] == "PROMISE_TO_PAY"
    assert row.payload["confidence"] == 0.9


def test_with_no_extractor_every_reply_reaches_a_human():
    """`--no-llm` is a real path here as it is for diagnosis. The keyword
    fallback sits below the confidence floor on purpose, so it labels a reply
    for a person rather than pretending to have understood it."""
    records = {}
    replies = {}
    for i, text in enumerate(["sir friday tak ho jayega",
                              "amount galat hai",
                              "payment kar diya"]):
        record = _store(_record(f"INV_70{i:02d}"))
        records[record.id] = record
        replies[record.id] = text

    result = process_replies(replies, records, extractor=None, at=_now())

    assert result.read == 3
    assert result.promises_made == 0
    assert result.low_confidence == 3
    with SessionLocal() as session:
        assert session.query(HumanQueueRow).count() == 3


def test_one_unreadable_reply_does_not_stop_the_others():
    """A batch that dies halfway leaves some customers actioned and some not,
    with no record of which."""
    records, replies = {}, {}
    for i in range(3):
        record = _store(_record(f"INV_70{i:02d}"))
        records[record.id] = record
        replies[record.id] = "friday tak ho jayega"

    class HalfBroken:
        def __init__(self):
            self.n = 0

        def __call__(self, reply, today=None, record=None):
            self.n += 1
            if self.n == 2:
                raise RuntimeError("provider blew up")
            return _reading(ReplyIntent.PROMISE_TO_PAY,
                            promised_date=(today + timedelta(days=5))
                            .strftime("%Y-%m-%d"))

    result = process_replies(replies, records, extractor=HalfBroken(), at=_now())
    assert result.read == 3
    assert result.promises_made == 2


def test_hinglish_is_read_by_the_fallback_at_all():
    """Not a claim that substring matching is good. A claim that the phrases
    the fixture actually produces are recognised at all when there is no model,
    so the degraded path degrades to 'a person reads it' rather than silence."""
    cases = {
        "mat bhejo baar baar": ReplyIntent.STOP_CONTACTING,
        "payment kar diya tha na": ReplyIntent.ALREADY_PAID,
        "PO number match nahi kar raha": ReplyIntent.DISPUTED,
        "galat number hai": ReplyIntent.WRONG_CONTACT,
    }
    for text, expected in cases.items():
        assert keyword_reading(text).intent is expected, text


def test_the_fallback_never_claims_enough_confidence_to_act():
    for text in ("friday tak ho jayega", "kuch bhi", "will pay"):
        assert keyword_reading(text).confidence < 0.6
