"""What a reply MEANS. Deterministic, and separate from what it says.

`intent.py` gets a label out of a sentence. This decides what the system does
about it, and the split is the same one `policy/` makes for RootCause: the model
labels, a table decides, the guardrails still hold. Nothing here calls a model,
and nothing here is a judgement call made at runtime — every row was written
before the batch ran, which is what makes the audit trail worth reading.

Five of the seven intents route to a human. That is not a gap. A partial-payment
offer is a commercial negotiation, a dispute is a conversation about what is
owed, and a claim of "already paid" is a reconciliation — none of them are
things an agent should settle on its own, and an agent that tried would be
wrong in the expensive direction.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

from ... import audit
from ...clock import now
from ...db import AtRiskRecordRow, CustomerRow, HumanQueueRow, SessionLocal
from ...enums import RecordState, ReplyIntent, Stage
from ...models import AtRiskRecord, ReplyReading
from ...promises import record_promise, validate_date
from .. import rules

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Effect:
    """What one intent does. Data, so the whole table reads at a glance."""

    outcome: str
    to_human: bool = False
    opt_out: bool = False
    promise: bool = False
    note: str = ""


# The table. Read it top to bottom and you have the entire conversation policy.
EFFECTS: dict[ReplyIntent, Effect] = {
    ReplyIntent.PROMISE_TO_PAY: Effect(
        outcome="PROMISED", promise=True,
        note="They named a date. Go quiet until it passes."),
    ReplyIntent.STOP_CONTACTING: Effect(
        outcome="OPTED_OUT", opt_out=True,
        note="Consent withdrawn. Never contact again, on any record."),
    ReplyIntent.ALREADY_PAID: Effect(
        outcome="CLAIMS_PAID", to_human=True,
        note="A claim about the past, not a promise about the future. "
             "Reconcile it; do not chase and do not mark it recovered."),
    ReplyIntent.DISPUTED: Effect(
        outcome="DISPUTED", to_human=True,
        note="A conversation about what is owed. Dunning it destroys the "
             "account and recovers nothing."),
    ReplyIntent.PARTIAL_PAYMENT_OFFER: Effect(
        outcome="PARTIAL_OFFER", to_human=True,
        note="Accepting less than the full amount is a commercial decision, "
             "and commercial decisions are never the agent's."),
    ReplyIntent.WRONG_CONTACT: Effect(
        outcome="WRONG_CONTACT", to_human=True,
        note="Contacting them again is contacting the wrong person again."),
    ReplyIntent.UNCLEAR: Effect(
        outcome="UNCLEAR", to_human=True,
        note="Not understood. A person reads it."),
}


@dataclass
class ReplyResult:
    read: int = 0
    promises_made: int = 0
    opted_out: int = 0
    to_human: int = 0
    low_confidence: int = 0
    rejected_dates: int = 0
    by_intent: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "read": self.read,
            "promises_made": self.promises_made,
            "opted_out": self.opted_out,
            "to_human": self.to_human,
            "low_confidence": self.low_confidence,
            "rejected_dates": self.rejected_dates,
            "by_intent": dict(sorted(self.by_intent.items())),
        }


def _confidence_floor() -> float:
    """The same floor that governs diagnosis, read from the same place.

    A reply the model half-understood must not move a record, for exactly the
    reason a diagnosis it half-believes must not move money. One threshold, one
    config key, one argument to have with a merchant.
    """
    return float(rules.threshold("confidence_floor", "minimum", default=0.6))


def apply_reading(record: AtRiskRecord, reading: ReplyReading, reply_text: str,
                  *, at: datetime | None = None,
                  result: ReplyResult | None = None) -> str:
    """Turn one reading into one effect. Returns the outcome recorded.

    Never raises. A reply that cannot be handled reaches a human, because the
    alternative — an exception halfway through a batch of replies — leaves some
    customers actioned and some not, with no record of which.
    """
    at = at or now()
    result = result if result is not None else ReplyResult()
    result.read += 1
    result.by_intent[reading.intent.value] = \
        result.by_intent.get(reading.intent.value, 0) + 1

    effect = EFFECTS.get(reading.intent, EFFECTS[ReplyIntent.UNCLEAR])

    # An honest low confidence is treated as an honest UNCLEAR. Note the one
    # exception below it: a request to stop is honoured whatever the model's
    # confidence, because the cost of wrongly staying silent is one unsent
    # message and the cost of wrongly continuing is a compliance breach.
    if (reading.confidence < _confidence_floor()
            and reading.intent is not ReplyIntent.STOP_CONTACTING):
        result.low_confidence += 1
        _audit(record.id, "LOW_CONFIDENCE", reading, reply_text, at,
               extra=f"Confidence {reading.confidence:.2f} is below the floor; "
                     f"routed to a human rather than acted on.")
        _to_human(record, f"Reply not confidently understood "
                          f"({reading.intent.value} @ {reading.confidence:.2f}): "
                          f"{reply_text[:160]}", at)
        result.to_human += 1
        return "LOW_CONFIDENCE"

    if effect.promise:
        parsed = _parse_date(reading.promised_date)
        when, why = validate_date(parsed, frm=at)
        if when is None:
            # The model read a date the system will not act on. This is the
            # guarantee doing its job, and it is logged as such rather than
            # quietly downgraded — a rejected date is the single most useful
            # line in this trail when somebody asks what the model is allowed
            # to decide.
            result.rejected_dates += 1
            _audit(record.id, "PROMISE_REJECTED", reading, reply_text, at,
                   extra=f"Extracted date refused by validation: {why}. "
                         f"The model may read a date; it may not set one.")
            _to_human(record, f"Promise with an unusable date ({why}): "
                              f"{reply_text[:160]}", at)
            result.to_human += 1
            return "PROMISE_REJECTED"

        record_promise(record.id, promised_for=when, amount=record.amount,
                       intent=reading.intent.value, confidence=reading.confidence,
                       reply_text=reply_text, at=at)
        result.promises_made += 1
        _audit(record.id, "PROMISED", reading, reply_text, at,
               extra=f"Committed to pay by {when:%Y-%m-%d}. Contact is held "
                     f"until then by guardrail 14.",
               deferred_until=when)
        return "PROMISED"

    if effect.opt_out:
        _opt_out(record.counterparty_id)
        result.opted_out += 1
        _audit(record.id, "OPTED_OUT", reading, reply_text, at,
               extra="Consent withdrawn. The consent guardrail now blocks every "
                     "contact to this customer, on every record they own, "
                     "permanently.")
        return "OPTED_OUT"

    if effect.to_human:
        _to_human(record, f"{reading.intent.value}: {reply_text[:160]}", at)
        result.to_human += 1
        _audit(record.id, effect.outcome, reading, reply_text, at,
               extra=effect.note)
        return effect.outcome

    _audit(record.id, effect.outcome, reading, reply_text, at, extra=effect.note)
    return effect.outcome


def process_replies(replies: dict[str, str], records: dict[str, AtRiskRecord],
                    *, extractor=None, at: datetime | None = None) -> ReplyResult:
    """Read a batch of replies and apply every one.

    The extractor is injected rather than constructed, so `--no-llm` is a real
    code path here as it is for diagnosis: with no extractor the keyword reading
    takes over, lands below the confidence floor, and every reply reaches a
    human. Degraded, and correct.
    """
    at = at or now()
    result = ReplyResult()

    # Read them all first, in as few requests as the extractor allows, and apply
    # them afterwards. Reading and applying were interleaved, which on a free
    # tier metered per minute made a tick's replies cost one paced call each -
    # and every one of those seconds came out of the same budget layer 2 is
    # already spending. Applying is unchanged and still one record at a time.
    readable = [(rid, text) for rid, text in replies.items()
                if records.get(rid) is not None and str(text).strip()]
    readings = _read_all([(rid, t) for rid, t in readable], records, extractor, at)

    for (record_id, text), reading in zip(readable, readings):
        record = records[record_id]
        if reading is None:
            reading = keyword_fallback(text)

        try:
            apply_reading(record, reading, text, at=at, result=result)
        except Exception as exc:  # noqa: BLE001
            log.warning("reply handling failed for %s: %s", record_id, exc)
            _to_human(record, f"Reply could not be handled ({exc!r}): "
                              f"{str(text)[:160]}", at)
            result.to_human += 1

    return result


def _read_all(items, records, extractor, at) -> list:
    """One reading per reply, in order, or None where the model could not.

    Batched when the extractor offers it, one at a time when it does not, and
    None on any failure so the keyword fallback takes over — the same three-way
    degradation diagnosis runs under, for the same reason: an unreadable reply
    must reach a person, never stop a tick.
    """
    if extractor is None or not items:
        return [None] * len(items)

    calls = [(text, at, records[record_id]) for record_id, text in items]

    many = getattr(extractor, "many", None)
    if callable(many):
        try:
            return many(calls)
        except Exception as exc:  # noqa: BLE001 - never kill the batch
            log.warning("batched reply extraction failed: %s", exc)

    readings = []
    for (record_id, _), call in zip(items, calls):
        try:
            readings.append(extractor(*call))
        except Exception as exc:  # noqa: BLE001
            log.warning("reply extraction failed for %s: %s", record_id, exc)
            readings.append(None)
    return readings


def keyword_fallback(text: str) -> ReplyReading:
    from .intent import keyword_reading

    return keyword_reading(text)


# --- effects ----------------------------------------------------------------


def _parse_date(raw: str | None) -> datetime | None:
    """Untrusted text to a datetime, or None. Refusing to parse is a valid
    answer and is handled by the caller as a rejected promise."""
    if not raw:
        return None
    from ...timeutil import IST

    try:
        parsed = datetime.fromisoformat(str(raw).strip()[:19])
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        # A bare date means the end of that business day, not midnight — a
        # promise "by Friday" is not broken at one minute past Friday morning.
        parsed = parsed.replace(hour=18, minute=0, tzinfo=IST)
    return parsed


def _opt_out(customer_id: str) -> None:
    with SessionLocal() as session:
        customer = session.get(CustomerRow, customer_id)
        if customer is not None:
            customer.opted_out = True
            session.commit()


def _to_human(record: AtRiskRecord, reason: str, at: datetime) -> None:
    """One open row per record, as everywhere else. A queue that grows by a row
    per tick is a queue nobody works."""
    with SessionLocal() as session:
        row = session.get(AtRiskRecordRow, record.id)
        # A reply can land after the money already has — the invoice was paid,
        # or a stopping rule closed the record, between the contact going out
        # and the answer coming back. Reading the reply is still worth doing;
        # putting a settled record on somebody's desk is not.
        if row is not None and RecordState(row.state).is_terminal:
            return

        existing = (session.query(HumanQueueRow)
                    .filter(HumanQueueRow.record_id == record.id)
                    .filter(HumanQueueRow.resolved_at.is_(None)).first())
        if existing is None:
            session.add(HumanQueueRow(record_id=record.id, reason=reason,
                                      amount=record.amount, raised_at=at))
        if row is not None:
            row.state = RecordState.ESCALATED.value
            row.next_action_at = None
        session.commit()


def _audit(record_id: str, outcome: str, reading: ReplyReading, text: str,
           at: datetime, *, extra: str = "", deferred_until=None) -> None:
    audit.log(
        record_id, Stage.REPLY, outcome,
        f"{reading.reasoning} {extra}".strip(),
        deferred_until=deferred_until,
        payload={
            "reply_text": str(text)[:500],
            "intent": reading.intent.value,
            "confidence": reading.confidence,
            "quote": reading.quote,
            "promised_date": reading.promised_date,
            "source": reading.source,
        },
    )
