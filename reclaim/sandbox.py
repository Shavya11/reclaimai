"""What a visitor types, run through the real machinery.

Two entry points over one set of decisions:

    preview(submission)   diagnose -> decide -> guardrail, and stop
    commit(submission)    the same, then persist and let the runner execute

**Preview and commit share the decision path, not a copy of it.** Preview calls
`diagnose_batch`, `decide` and `gate.run` — the same three functions the runner
calls, in the same order, with the same arguments. Commit hands the record to
`run_batch` and lets the runner do all of it. What preview omits is execution,
which is the definition of a preview rather than a second opinion about what
should happen. If those two ever disagree about a label, an action or a verdict,
it is because one of those three functions changed, and both sides changed with it.

**Preview writes nothing, and needs no scratch database to be sure of it.**
`use_database` rebinds a module global, is documented as not thread-safe, and is
safe today only because the busy flag serialises one long batch at a time. A
preview is short, interactive and concurrent — the wrong shape entirely. It does
not need one: diagnosis, the policy table and the gate are pure, and the gate's
context (contact history, open promises, executed keys) is a READ. There is
nothing to isolate because there is nothing being written.

A committed record enters through `run_batch(only={id})` so it inherits the gate,
the idempotency key and the audit rows rather than being handed a private version
of any of them. It is attached to a customer from the seeded batch on purpose:
consent, DND and the seven-day frequency cap are customer-level, so borrowing a
customer with history is what lets guardrail 7 fire on a visitor's submission at
all. A record with a brand new customer can never be blocked for contacting
somebody too often, which would quietly make the demo look safer than it is.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from pydantic import Field

from .clock import now
from .enums import LeakType, RecordState
from .models import AtRiskRecord, _Base
from .provenance import mark, next_user_id

log = logging.getLogger(__name__)

# The customer a submission is attached to unless one is named. Present in every
# seeded batch, and carries lifetime history, so the frequency cap and the
# consent rules have something real to judge.
DEFAULT_CUSTOMER = "CUST_4000"

PREVIEW_ID = "USR_preview"


class Submission(_Base):
    """What the dashboard sends. Deliberately small: the visitor describes a
    failure, they do not get to choose what happens about it."""

    text: str = Field(default="", max_length=2000)
    error_code: str = ""
    error_reason: str = ""
    amount_paise: int = Field(default=250_000, ge=0, le=10_000_000_00)
    leak_type: LeakType = LeakType.FAILED_PAYMENT
    customer_id: str = DEFAULT_CUSTOMER
    issuer_bank: str = "HDFC"
    method: str = "card"
    # Forces the fallback chain: layer 2 unavailable, so an unmapped error has
    # to reach UNKNOWN and a human rather than a confident-looking guess.
    without_model: bool = False


@dataclass
class Stage:
    """One card in the trace strip.

    `decided_by` is the whole point of rendering this: a reader can see at a
    glance that the model badge appears on exactly one card, which is CLAUDE.md's
    one rule made visible instead of claimed.
    """

    stage: str
    decided_by: str  # detector | model | table | gate | runner
    output: str
    detail: str = ""
    why: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"stage": self.stage, "decided_by": self.decided_by,
                "output": self.output, "detail": self.detail, "why": self.why}


@dataclass
class Trace:
    record_id: str
    stages: list[Stage] = field(default_factory=list)
    committed: bool = False
    verdict: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"record_id": self.record_id, "committed": self.committed,
                "verdict": self.verdict,
                "trace": [s.as_dict() for s in self.stages]}


def _error_block(sub: Submission) -> dict[str, Any] | None:
    """The visitor's words in the shape the deterministic map keys off.

    `reason` is what layer 1 looks up, so a visitor who types a real Razorpay
    reason gets a layer-1 hit and a visitor who describes the problem in English
    falls through to layer 2. Both are the demo; neither is a special case.
    """
    if sub.leak_type is LeakType.ABANDONED_CART:
        return None
    reason = (sub.error_reason or "").strip()
    if not reason and sub.text:
        # A bare description is not a reason code. Left unmapped on purpose so
        # it falls through to layer 2 rather than being pattern-matched here —
        # a second, sloppier deterministic map is the last thing this needs.
        reason = ""
    return {
        "code": (sub.error_code or "BAD_REQUEST_ERROR").strip(),
        "reason": reason,
        "source": "customer",
        "step": "payment_authorization",
        "description": sub.text.strip() or "Payment failed.",
    }


def build_record(sub: Submission, record_id: str) -> AtRiskRecord:
    signals: dict[str, Any] = mark({
        "issuer_bank": sub.issuer_bank,
        "method": sub.method,
        "attempt_number": 1,
        "error": _error_block(sub),
        "customer_history": {
            "successful_payments_lifetime": 3,
            "failed_payments_last_30d": 1,
            "same_instrument_succeeded_before": True,
            "last_successful_at": None,
        },
        "submitted_text": sub.text.strip(),
    })
    if sub.leak_type is LeakType.ABANDONED_CART:
        signals["cart_age_minutes"] = 120
    else:
        signals["card_network"] = "Visa"
        signals["card_type"] = "debit"

    return AtRiskRecord(
        id=record_id,
        leak_type=sub.leak_type,
        amount=sub.amount_paise,
        counterparty_id=sub.customer_id or DEFAULT_CUSTOMER,
        source_ref=f"pay_sandbox_{record_id.lower()}",
        detected_at=now() - timedelta(minutes=5),
        raw_signals=signals,
        state=RecordState.AT_RISK,
    )


def _diagnose(record: AtRiskRecord, *, without_model: bool):
    """Layer 1, then layer 2, then the floor — through the same engine the batch
    uses, so a change to the fallback chain reaches the sandbox for free."""
    from .brain.diagnosis.deterministic import diagnose as layer1
    from .brain.diagnosis.engine import diagnose_batch

    layer1_hit = layer1(record)
    llm = None
    if not without_model:
        from .api.app import _llm  # the same resolver the batch endpoint uses

        try:
            llm = _llm()
        except Exception as exc:  # noqa: BLE001 — a missing key is not an error
            log.debug("sandbox: no layer 2 available: %s", exc)

    diagnoses, signals = diagnose_batch([record], {}, llm=llm)
    return layer1_hit, diagnoses[record.id], signals.get(record.id)


def _evaluate(record: AtRiskRecord, sub: Submission) -> tuple[Trace, Any, Any]:
    """The decision path, stopping before anything is executed."""
    from .brain import gate
    from .brain.policy import decide
    from .db import CustomerRow, SessionLocal
    from .executor.actions import executed_keys

    frm = now()
    trace = Trace(record_id=record.id)

    trace.stages.append(Stage(
        "DETECT", "detector", record.leak_type.value,
        f"{record.id} · {record.counterparty_id}",
        why={"amount_paise": record.amount,
             "error": record.raw_signals.get("error")}))

    layer1_hit, diagnosis, cohort = _diagnose(
        record, without_model=sub.without_model)

    if layer1_hit is not None:
        trace.stages.append(Stage(
            "DIAGNOSE L1", "table", layer1_hit.root_cause.value,
            layer1_hit.reasoning,
            why={"evidence_used": layer1_hit.evidence_used, "confidence": 1.0}))
    else:
        trace.stages.append(Stage(
            "DIAGNOSE L1", "table", "NO MATCH",
            "No error reason in the deterministic map — falls through to layer 2.",
            why={"error_reason":
                 (record.raw_signals.get("error") or {}).get("reason") or ""}))
        trace.stages.append(Stage(
            "DIAGNOSE L2", "model", diagnosis.root_cause.value,
            diagnosis.reasoning,
            why={"confidence": diagnosis.confidence, "source": diagnosis.source,
                 "evidence_used": diagnosis.evidence_used,
                 "cohort": cohort.as_dict() if hasattr(cohort, "as_dict") else None}))

    action = decide(record, diagnosis, frm=frm, anchor=record.detected_at)
    trace.stages.append(Stage(
        "POLICY", "table", action.action_type.value, action.rationale,
        why={"policy_ref": action.policy_ref,
             "attempt": action.attempt_number,
             "scheduled_for": action.scheduled_for.isoformat(),
             "channel": action.channel.value if action.channel else None}))

    with SessionLocal() as session:
        customer = (session.query(CustomerRow)
                    .filter(CustomerRow.id == record.counterparty_id).first())
        customers = {customer.id: customer} if customer else {}
        report = gate.run([record], {record.id: diagnosis}, [action], customers,
                          frm=frm, executed_keys=executed_keys())

    outcome = report.outcomes[0] if report.outcomes else None
    if outcome is None:
        # Not due yet: the schedule parked it. A real state, not a failure.
        trace.stages.append(Stage(
            "GUARDRAILS", "gate", "NOT DUE",
            f"{action.action_type.value} is scheduled for "
            f"{action.scheduled_for:%Y-%m-%d %H:%M} IST and waits until then.",
            why={"scheduled_for": action.scheduled_for.isoformat()}))
        trace.verdict = "SCHEDULED"
        return trace, diagnosis, action

    verdict = outcome.result
    blocks = [{"guardrail": v.guardrail, "reason": v.reason,
               "requires_human": v.requires_human,
               "closes_record": v.closes_record,
               "deferred_until": (v.deferred_until.isoformat()
                                  if v.deferred_until else None)}
              for v in verdict.violations]
    trace.stages.append(Stage(
        "GUARDRAILS", "gate", "ALLOWED" if verdict.allowed else "BLOCKED",
        "All guardrails passed." if verdict.allowed
        else f"Blocked by {', '.join(b['guardrail'] for b in blocks)}.",
        why={"blocked_by": blocks, "idempotency_key": action.idempotency_key}))
    trace.verdict = "ALLOWED" if verdict.allowed else (
        "HUMAN" if verdict.requires_human else "BLOCKED")
    return trace, diagnosis, action


def preview(sub: Submission) -> dict[str, Any]:
    """What would happen. Nothing is written, including the record itself."""
    record = build_record(sub, PREVIEW_ID)
    trace, _diag, _action = _evaluate(record, sub)
    return trace.as_dict()


def commit(sub: Submission) -> dict[str, Any]:
    """The same submission, for real: persisted, then run by the runner.

    The trace is read back off the audit log rather than reported from memory,
    for the reason the scoreboard is computed from stored rows — a claim about
    what happened that cannot be recovered from what was written down is a claim
    nobody should believe, including us.
    """
    from .db import SessionLocal, init_db
    from .repository import save_records
    from .runner import run_batch

    init_db()
    with SessionLocal() as session:
        record_id = next_user_id(session)

    record = build_record(sub, record_id)
    save_records([record])

    result = run_batch(reseed=False, only={record_id}, settle=False,
                       llm=None if sub.without_model else _resolve_llm())
    _settle_one(record_id)

    trace = _trace_from_audit(record_id)
    trace.committed = True
    payload = trace.as_dict()
    payload["batch"] = result.as_dict()
    return payload


def _resolve_llm():
    try:
        from .api.app import _llm

        return _llm()
    except Exception as exc:  # noqa: BLE001
        log.debug("sandbox commit: no layer 2 available: %s", exc)
        return None


_DECIDED_BY = {
    "DETECT": "detector",
    "DIAGNOSE": "model",
    "DECIDE": "table",
    "GUARDRAIL": "gate",
    "EXECUTE": "runner",
    "REPLY": "model",
}


def _trace_from_audit(record_id: str) -> Trace:
    from sqlalchemy import asc

    from .db import AuditLogRow, SessionLocal

    trace = Trace(record_id=record_id)
    with SessionLocal() as session:
        rows = (session.query(AuditLogRow)
                .filter(AuditLogRow.record_id == record_id)
                .order_by(asc(AuditLogRow.id)).all())
        for row in rows:
            decided = _DECIDED_BY.get(row.stage, "runner")
            # A diagnosis that layer 1 resolved is a table lookup, not a model
            # call, and badging it as the model would put the model's badge on a
            # card it never touched — the one thing this strip exists to show.
            if row.stage == "DIAGNOSE" and (row.payload or {}).get("source") != "llm":
                decided = "table"
            trace.stages.append(Stage(
                row.stage, decided, row.outcome or "", row.reason or "",
                why={k: v for k, v in (row.payload or {}).items()}
                | ({"guardrail": row.guardrail} if row.guardrail else {})))
        trace.verdict = trace.stages[-1].output if trace.stages else ""
    return trace


# One-click inputs for the dashboard. Three of them are chosen to show a
# different path through the same machinery rather than three flavours of the
# same one: a layer-1 hit, a layer-2 fall-through, and a cause the policy table
# refuses to act on at all.
PRESETS: list[dict[str, Any]] = [
    {
        "label": "Card expired",
        "hint": "a real Razorpay reason — layer 1 resolves it, the model is never asked",
        "submission": {"error_reason": "card_expired",
                       "error_code": "BAD_REQUEST_ERROR",
                       "text": "Your card has expired. Please use a different card.",
                       "amount_paise": 249_900},
    },
    {
        "label": "Bank just declined it",
        "hint": "ambiguous — layer 1 has no answer, so layer 2 is consulted",
        "submission": {"error_reason": "payment_failed",
                       "error_code": "BAD_REQUEST_ERROR",
                       "text": "The bank declined this transaction.",
                       "amount_paise": 415_000},
    },
    {
        "label": "Described in English",
        "hint": "no reason code at all — the model reads the sentence",
        "submission": {"text": "Customer says the payment failed twice last night, "
                               "salary comes on the 1st.",
                       "amount_paise": 780_000},
    },
    {
        "label": "Above the authority ceiling",
        "hint": "diagnosed fine, then refused — the value ceiling sends it to a human",
        "submission": {"error_reason": "payment_failed",
                       "text": "High-value order declined by the bank.",
                       "amount_paise": 9_500_000},
    },
    {
        "label": "Abandoned cart",
        "hint": "no payment was ever attempted — a different leak type entirely",
        "submission": {"leak_type": "ABANDONED_CART",
                       "text": "Order created, checkout never completed.",
                       "amount_paise": 189_900},
    },
]


def _settle_one(record_id: str) -> None:
    """Give the committed record an outcome, and only that record.

    **A submission has no hidden ground truth.** The seeded batch has one because
    the generator planted it; a visitor typing "card expired" is not concealing a
    different answer we could be wrong about — their description IS the fact of
    the matter. So the diagnosed cause is what the outcome is drawn against, and
    the consequence is stated rather than left to be discovered: a committed
    record can never be a diagnosis error, so it must never be counted in
    accuracy. It is not — `/api/diagnosis` scores a freshly generated batch and
    cannot see the database at all.

    Settling only this record matters as much as settling it. `settle()` walks
    every pending intervention, so calling it unfiltered would settle the seeded
    batch as a side effect of somebody pressing a demo button, and move the
    published figures — the one thing the `USR_` split exists to prevent.

    Without an outcome the record can never resolve and sits in the human queue
    for ever, which is the bug 6.1 already fixed once for a different reason.
    """
    from .config import settings
    from .enums import RootCause
    from .settlement import settle

    cause = _diagnosed_cause(record_id)
    try:
        settle({record_id: cause or RootCause.UNKNOWN},
               seed=settings.seed, only={record_id})
    except Exception as exc:  # noqa: BLE001 — an unsettled record is not a failed commit
        log.warning("sandbox: %s committed but did not settle: %r", record_id, exc)


def _diagnosed_cause(record_id: str):
    from sqlalchemy import desc

    from .db import AuditLogRow, SessionLocal
    from .enums import RootCause, Stage

    with SessionLocal() as session:
        row = (session.query(AuditLogRow)
               .filter(AuditLogRow.record_id == record_id)
               .filter(AuditLogRow.stage == Stage.DIAGNOSE.value)
               .order_by(desc(AuditLogRow.id)).first())
    if row is None:
        return None
    try:
        return RootCause(row.outcome)
    except ValueError:
        return None


# --- 7.8 reply / promise mode -------------------------------------------------


def read_reply(text: str, *, without_model: bool = False) -> dict[str, Any]:
    """A customer's reply, read the way the batch reads one — and stopped there.

    The interesting output is what does NOT happen. A promise the system accepts
    buys silence: guardrail 14 refuses every contact until the date passes, and a
    dashboard cannot render silence as activity, so it is spelled out here.

    Three deterministic gates sit between the model and any effect, and each is
    its own card because each is a different refusal:
      * the confidence floor, below which a reading reaches a person instead;
      * `validate_date`, which refuses a date the system will not act on — the
        model may READ a date, it may not SET one;
      * the effects table, which is data and can be read top to bottom.
    """
    from .brain.conversation.handler import EFFECTS, _confidence_floor
    from .brain.conversation.intent import keyword_reading
    from .enums import ReplyIntent
    from .promises import validate_date

    frm = now()
    trace = Trace(record_id="—")
    reply = (text or "").strip()

    trace.stages.append(Stage(
        "REPLY", "detector", "RECEIVED", reply[:180] or "(empty)",
        why={"characters": len(reply)}))

    reading = None
    if not without_model:
        try:
            extractor = _build_extractor()
            if extractor is not None:
                reading = extractor.read(reply, today=frm)
        except Exception as exc:  # noqa: BLE001 — never raise at a reader
            log.debug("sandbox reply: extractor unavailable: %s", exc)

    if reading is None:
        reading = keyword_reading(reply)
        trace.stages.append(Stage(
            "READ", "table", reading.intent.value,
            reading.reasoning,
            why={"confidence": reading.confidence, "source": reading.source,
                 "quote": reading.quote}))
    else:
        trace.stages.append(Stage(
            "READ", "model", reading.intent.value, reading.reasoning,
            why={"confidence": reading.confidence, "source": reading.source,
                 "quote": reading.quote,
                 "promised_date": reading.promised_date}))

    floor = _confidence_floor()
    below = (reading.confidence < floor
             and reading.intent is not ReplyIntent.STOP_CONTACTING)
    trace.stages.append(Stage(
        "CONFIDENCE", "gate", "BELOW FLOOR" if below else "ABOVE FLOOR",
        f"{reading.confidence:.2f} against a floor of {floor:.2f}."
        + (" A person reads it instead." if below else "")
        + (" A request to stop is honoured at any confidence — the cost of"
           " wrongly staying silent is one unsent message."
           if reading.intent is ReplyIntent.STOP_CONTACTING else ""),
        why={"confidence": reading.confidence, "floor": floor}))

    effect = EFFECTS.get(reading.intent, EFFECTS[ReplyIntent.UNCLEAR])

    if below:
        trace.stages.append(Stage(
            "EFFECT", "table", "TO A HUMAN",
            "Below the floor, so the reading is a label for a person rather "
            "than an instruction to the agent.", why={}))
        trace.verdict = "HUMAN"
        return trace.as_dict()

    if effect.promise:
        parsed = _parse_promise_date(reading.promised_date)
        when, why = validate_date(parsed, frm=frm)
        trace.stages.append(Stage(
            "DATE", "gate", "ACCEPTED" if when else "REFUSED",
            (f"{when:%d %b %Y} is a date the system will act on."
             if when else
             f"Refused: {why}. The model may read a date; it may not set one."),
            why={"read_as": reading.promised_date, "parsed": str(parsed),
                 "verdict": why}))
        if when is None:
            trace.stages.append(Stage(
                "EFFECT", "table", "TO A HUMAN",
                "A promise with an unusable date is not a promise the agent "
                "can keep quiet for.", why={}))
            trace.verdict = "HUMAN"
            return trace.as_dict()
        trace.stages.append(Stage(
            "EFFECT", "table", "AGENT GOES SILENT",
            f"{effect.note} Guardrail 14 refuses every contact until "
            f"{when:%d %b}, then checks whether the money arrived.",
            why={"outcome": effect.outcome, "silent_until": when.isoformat(),
                 "guardrail": "promise_window"}))
        trace.verdict = "PROMISED"
        return trace.as_dict()

    trace.stages.append(Stage(
        "EFFECT", "table",
        "TO A HUMAN" if effect.to_human else effect.outcome,
        effect.note,
        why={"outcome": effect.outcome, "opt_out": effect.opt_out,
             "to_human": effect.to_human}))
    trace.verdict = "HUMAN" if effect.to_human else effect.outcome
    return trace.as_dict()


def _build_extractor():
    from .brain.conversation import build_extractor

    extractor = build_extractor()
    return extractor if getattr(extractor, "available", False) else None


def _parse_promise_date(raw: str | None):
    from .brain.conversation.handler import _parse_date

    return _parse_date(raw)


REPLY_PRESETS: list[dict[str, str]] = [
    {"label": "Hinglish promise",
     "hint": "how these actually arrive — a date, in two languages",
     "text": "paisa 5 tareek ko bhej dunga bhai, thoda time chahiye"},
    {"label": "Plain promise",
     "hint": "accepted, and the agent goes quiet until then",
     "text": "I'll pay this on Friday, sorry for the delay."},
    {"label": "A date nobody will act on",
     "hint": "the model may read a date; validate_date decides whether it counts",
     "text": "I'll settle this sometime next year, maybe December 2031."},
    {"label": "Stop contacting me",
     "hint": "honoured at any confidence — consent is not a probability",
     "text": "stop messaging me, remove my number"},
    {"label": "Already paid",
     "hint": "a claim about the past, not a promise — a person reconciles it",
     "text": "I already paid this yesterday by UPI."},
    {"label": "Disputed",
     "hint": "dunning it destroys the account and recovers nothing",
     "text": "This charge is wrong, I never ordered that."},
]


# --- 7.9 the guardrail simulator ----------------------------------------------


class Hypothetical(_Base):
    """A situation to point the fourteen rules at.

    Every field here is something a merchant can argue about, which is the
    point: the guardrails are a policy position, and a policy position you
    cannot poke is a claim.
    """

    action_type: str = "SEND_LINK"
    amount_paise: int = Field(default=250_000, ge=0, le=10_000_000_00)
    attempt_number: int = Field(default=1, ge=1, le=10)
    hour_ist: int = Field(default=11, ge=0, le=23)
    contacts_last_7d: int = Field(default=0, ge=0, le=20)
    hours_since_last_contact: float = Field(default=72.0, ge=0)
    record_age_days: float = Field(default=2.0, ge=0)
    diagnosis_confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    actions_today: int = Field(default=0, ge=0)
    record_state: str = "AT_RISK"
    leak_type: LeakType = LeakType.FAILED_PAYMENT
    opted_out: bool = False
    on_dnd: bool = False
    autopilot_enabled: bool = True
    inside_promise_window: bool = False
    already_executed: bool = False


def simulate_guardrails(h: Hypothetical) -> dict[str, Any]:
    """All fourteen rules, on one hypothetical action.

    The greens matter as much as the blocks. A screen that showed only refusals
    would suggest the gate is a filter with an opinion; showing all fourteen
    every time is what makes "eleven passed, three refused" a measurement.

    Nothing here is written, and nothing here is a special evaluation path: this
    is `evaluate_all` over the same `REGISTRY` the batch uses. A rule added as a
    file but never registered is invisible to both, which is what `verify`
    checks separately.
    """
    from datetime import timedelta as _td

    from .brain.guardrails.base import GuardrailContext, evaluate_all
    from .brain.guardrails.registry import REGISTRY
    from .enums import ActionType, Channel
    from .models import ProposedAction

    at = now().replace(hour=h.hour_ist, minute=0, second=0, microsecond=0)
    try:
        action_type = ActionType(h.action_type)
    except ValueError:
        action_type = ActionType.SEND_LINK

    action = ProposedAction(
        record_id=PREVIEW_ID,
        action_type=action_type,
        channel=Channel.EMAIL if action_type is not ActionType.SILENT_RETRY else None,
        scheduled_for=at,
        attempt_number=h.attempt_number,
        policy_ref=f"{h.leak_type.value}:SANDBOX",
        rationale="A hypothetical, for the guardrail simulator.",
        amount=h.amount_paise,
    )

    ctx = GuardrailContext(
        now=at,
        autopilot_enabled=h.autopilot_enabled,
        opted_out=h.opted_out,
        on_dnd=h.on_dnd,
        contacts_last_7d=h.contacts_last_7d,
        last_contact_at=at - _td(hours=h.hours_since_last_contact),
        executed_keys=frozenset({action.idempotency_key}
                                if h.already_executed else ()),
        record_state=h.record_state,
        record_age_days=h.record_age_days,
        diagnosis_confidence=h.diagnosis_confidence,
        actions_today=h.actions_today,
        # `promised_for` is a datetime, not a string, and the key name is the
        # one gate.py uses. Both matter: a guardrail reads its context by name
        # and a simulator that invents its own would show a rule passing that
        # the batch would have blocked, which is worse than showing nothing.
        extra={"leak_type": h.leak_type.value,
               "promised_for": (at + _td(days=3))
               if h.inside_promise_window else None},
    )

    result = evaluate_all(action, ctx)
    blocked = {v.guardrail: v for v in result.violations}

    rules = []
    for guardrail in REGISTRY:
        violation = blocked.get(guardrail.name)
        rules.append({
            "guardrail": guardrail.name,
            "verdict": "BLOCK" if violation else "PASS",
            "reason": violation.reason if violation else "",
            "requires_human": bool(violation and violation.requires_human),
            "closes_record": bool(violation and violation.closes_record),
            "deferred_until": (violation.deferred_until.isoformat()
                               if violation and violation.deferred_until else None),
        })

    return {
        "allowed": result.allowed,
        "requires_human": result.requires_human,
        "passed": sum(1 for r in rules if r["verdict"] == "PASS"),
        "blocked": sum(1 for r in rules if r["verdict"] == "BLOCK"),
        "total": len(rules),
        "idempotency_key": action.idempotency_key,
        "rules": rules,
    }


GUARDRAIL_SCENARIOS: list[dict[str, Any]] = [
    {"label": "A normal afternoon",
     "hint": "nothing to refuse — all fourteen pass",
     "hypothetical": {}},
    {"label": "2am",
     "hint": "quiet hours: nobody is woken up for a payment link",
     "hypothetical": {"hour_ist": 2}},
    {"label": "Third contact this week",
     "hint": "the seven-day frequency cap, per customer, across every record",
     "hypothetical": {"contacts_last_7d": 2}},
    {"label": "₹95,000",
     "hint": "above the authority ceiling for a consumer card — a person decides",
     "hypothetical": {"amount_paise": 9_500_000}},
    {"label": "Inside a promise window",
     "hint": "somebody named a date; guardrail 14 buys them silence until it",
     "hypothetical": {"inside_promise_window": True}},
    {"label": "Customer opted out",
     "hint": "consent withdrawn closes the record rather than deferring it",
     "hypothetical": {"opted_out": True}},
    {"label": "Autopilot off",
     "hint": "the panic button: every action refused, including scheduled ones",
     "hypothetical": {"autopilot_enabled": False}},
    {"label": "Already executed",
     "hint": "the same key twice is a replay, and replays never execute",
     "hypothetical": {"already_executed": True}},
]
