"""Layer 2: the model, on the ~40% of records an error string cannot resolve.

Forced tool use, because a closed schema is what makes hallucination harmless:
the model cannot invent a root cause, only pick a wrong one from a fixed list —
and the policy table and guardrails below it still hold either way.

Three cost controls, all required for a 120-record batch to finish in seconds:
signature caching (identical error shapes are one call, not forty), a semaphore
so we do not open 120 sockets at once, and a fallback chain that degrades to
UNKNOWN rather than failing the batch.

The model NEVER decides the action, the amount, the timing or the recipient.
It returns a label and a confidence. That is the entire contract.
"""

import copy
import hashlib
import json
import logging
import threading
from typing import Any

from pydantic import ValidationError

from ...config import settings
from ...enums import RootCause
from ...models import AtRiskRecord, Diagnosis
from .cohort import CohortSignal

log = logging.getLogger(__name__)

MAX_CONCURRENCY = 8
MAX_TOKENS = 2048

DIAGNOSIS_TOOL: dict[str, Any] = {
    "name": "record_diagnosis",
    "description": (
        "Record the single most likely root cause of one failed payment, with an "
        "honest confidence score and the evidence that drove the decision."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "root_cause": {
                "type": "string",
                "enum": [c.value for c in RootCause],
                "description": "Exactly one cause from the closed list.",
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Below 0.6 routes to a human. Use it honestly.",
            },
            "reasoning": {
                "type": "string",
                "description": "One or two sentences a support agent could read.",
            },
            "recoverable": {
                "type": "boolean",
                "description": "Could any automated retry or link plausibly recover this?",
            },
            "evidence_used": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The specific fields that drove the decision.",
            },
        },
        "required": [
            "root_cause",
            "confidence",
            "reasoning",
            "recoverable",
            "evidence_used",
        ],
    },
}

SYSTEM_PROMPT = """You are a payments failure analyst for an Indian merchant on Razorpay.
Classify ONE failed payment into exactly one root cause.

Rules, in order of precedence:

- Choose UNKNOWN if the evidence genuinely does not distinguish between causes.
  Do not guess in order to appear confident. An honest UNKNOWN reaches a human;
  a confident wrong answer reaches somebody's bank account.
- If the cohort failure rate for this issuer is far above the baseline, prefer
  BANK_DOWNTIME even when the error text says "declined". A bank outage looks
  exactly like a decline from a single record's point of view.
- If the same instrument succeeded before, do NOT choose INVALID_INSTRUMENT or
  EXPIRED_INSTRUMENT unless the error text explicitly says so. A card that
  worked last month is not suddenly a bad card number.
- RISK_DECLINE and MANDATE_REVOKED are never recoverable by retry. Retrying a
  risk decline looks like card testing and can cost the merchant its account.
- Every record you see here already has a generic error string — that is the
  only reason you are being asked. So the generic wording of `error` is not
  itself evidence of anything, and "the error is generic" is not a reason to
  answer UNKNOWN. Weigh the other fields; they are why they are here.
- A generic decline on an instrument that has succeeded before, attempted late
  at night (`attempted_hour_ist` >= 21) within about a week of month-end
  (`days_to_month_end` <= 8), is INSUFFICIENT_FUNDS unless something else
  explains it better. This is the ordinary shape of a consumer running short
  before salary, and it is the most common failure of its kind in Indian
  payments. Say so with the confidence you actually hold — if those conditions
  all hold, that is a real lean, not a coin toss.
- UNKNOWN remains correct when the fields genuinely conflict or are absent: no
  history, mid-month, mid-afternoon, nothing to go on.
- Set confidence below 0.6 whenever you are genuinely unsure. That routes the
  record to human review, which is the correct outcome for an ambiguous case.

You are producing a label only. You do not decide what happens next."""


RECEIVABLES_PROMPT = """You are a receivables analyst for an Indian B2B supplier on Razorpay.
Classify ONE overdue invoice into exactly one root cause.

This is not a failed payment. Nothing declined. No error code exists, because
nobody attempted anything — the invoice was issued and has not been paid. The
reason is organisational, and it has to be read out of ageing, history, partial
payment and what the buyer's AP contact has or has not said.

Rules, in order of precedence:

- If the buyer has already replied and their reply is in the context, that reply
  outranks every statistical signal. Somebody telling you why they have not paid
  is better evidence than an ageing bucket.
- INVOICE_DISPUTED whenever the amount, the goods or the terms are contested,
  including a purchase-order or GST mismatch. A dispute is never a collections
  problem and must reach a person.
- AWAITING_APPROVAL when the invoice is inside the buyer's own approval process
  and is not yet late by THEIR terms. A buyer whose `avg_days_to_pay` is 45 and
  whose invoice is 30 days old is not delinquent; they are slow, and slow on
  their normal schedule.
- INVOICE_NOT_RECEIVED when `reminders_sent` is 0 and there is no reply, no
  partial payment and no history of lateness. The commonest and most
  embarrassing cause of an unpaid invoice is that it never arrived.
- BUYER_CASH_CRUNCH when a buyer who normally pays has gone quiet, is paying
  other invoices partially, or has stretched well past their own average. A
  partial payment is the strongest single tell: somebody who pays part of a bill
  is not disputing it and has not lost it.
- PAYMENT_STALLED when it is simply late, past their own average, with no
  dispute, no promise and no explanation. This is the default for a genuine
  no-response, and it is the one the dunning ladder is built for.
- UNKNOWN when the signals genuinely conflict, or when a large amount is late
  with nothing in the history to read. Do not guess to appear useful. An honest
  UNKNOWN reaches a human; a confident wrong answer duns a customer who was
  waiting on your own credit note.
- Set confidence below 0.6 whenever you are genuinely unsure. That routes the
  invoice to human review, which is the correct outcome for an ambiguous one.

Judge lateness against `avg_days_to_pay` for THIS buyer, not against the due
date alone. A 60-day payer at day 50 and a 15-day payer at day 50 are different
situations and the second is the one to worry about.

You are producing a label only. You do not decide what happens next."""


class CachedDiagnoser:
    """Everything about an LLM call in this system that is not the provider.

    Which vendor answers is a deployment detail; the caching, the concurrency
    cap and the never-raise fallback are properties of the layer itself. They
    live here once so a second provider cannot quietly drift from the first.

    V2 added a second *job* as well as a second provider — labelling a customer
    reply — and it belongs here for the same reason. Everything below is
    identical for both: identical inputs cost one call, no more than
    MAX_CONCURRENCY sockets open at a time, and a failure returns None so the
    caller degrades rather than the batch dying. A copy of this class for the
    conversation layer would be a copy of the rule that a model failure must
    never stop a batch, and copies of that rule go stale.

    Subclasses supply a client, `_ask`, and — where the default is wrong — a
    `_signature`. Nothing else.
    """

    def __init__(self, client=None, model: str = "") -> None:
        self.model = model
        self._semaphore = threading.Semaphore(MAX_CONCURRENCY)
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()
        self.calls = 0
        self.cache_hits = 0
        self._client = client

    @property
    def available(self) -> bool:
        return self._client is not None

    def _signature(self, *args) -> str:
        """What makes two calls the same call. Diagnosis keys on the fields that
        change the answer; the conversation layer keys on the reply text."""
        return signature(*args)

    def __call__(self, *args):
        """Matches the callable protocol the caller injects. Returns None so the
        caller falls through to its own fallback. Never raises, never blocks a
        batch."""
        if not self.available:
            return None

        key = self._signature(*args)
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self.cache_hits += 1
                return hit.model_copy()

        try:
            with self._semaphore:
                answer = self._ask(*args)
        except Exception as exc:  # noqa: BLE001 - API down must not kill the batch
            log.warning("%s call failed: %s", type(self).__name__, exc)
            return None

        if answer is not None:
            with self._lock:
                self._cache[key] = answer
        return answer

    def _ask(self, *args):
        raise NotImplementedError


class LLMDiagnoser(CachedDiagnoser):
    def __init__(self, client=None, model: str | None = None) -> None:
        super().__init__(client, model or settings.anthropic_model)
        if self._client is None and settings.has_anthropic:
            import anthropic

            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def _ask(self, record, signal=None) -> Diagnosis | None:
        self.calls += 1
        response = self._client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=prompt_for(record),
            tools=[tool_for(record)],
            tool_choice={"type": "tool", "name": "record_diagnosis"},
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(build_context(record, signal), indent=2),
                }
            ],
        )
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                return _validate(block.input)
        return None


def _validate(payload: dict) -> Diagnosis | None:
    """A schema violation is not a crash and not a guess. It is an UNKNOWN that
    a human picks up."""
    try:
        return Diagnosis(
            root_cause=RootCause(payload["root_cause"]),
            confidence=float(payload["confidence"]),
            reasoning=str(payload["reasoning"]),
            recoverable=bool(payload["recoverable"]),
            evidence_used=[str(e) for e in payload.get("evidence_used", [])],
            source="llm",
        )
    except (KeyError, ValueError, TypeError, ValidationError) as exc:
        log.warning("LLM returned an unusable diagnosis: %s", exc)
        return None


def tool_for(record: AtRiskRecord) -> dict[str, Any]:
    """The diagnosis tool with the enum narrowed to what this leak type can
    actually be.

    A closed enum is what makes a hallucination harmless. A closed enum that
    only contains the reachable members makes it harmless AND smaller: the
    receivables model is not offered EXPIRED_INSTRUMENT at all, so it cannot
    return it, so the policy table never has to have a row for it. The guarantee
    is the same one V1 shipped, drawn tighter now that the domain has two halves.
    """
    from ...enums import CAUSES_FOR_LEAK

    allowed = CAUSES_FOR_LEAK.get(record.leak_type)
    if not allowed:
        return DIAGNOSIS_TOOL

    tool = copy.deepcopy(DIAGNOSIS_TOOL)
    tool["input_schema"]["properties"]["root_cause"]["enum"] = [
        c.value for c in RootCause if c in allowed
    ]
    return tool


def prompt_for(record: AtRiskRecord) -> str:
    """An invoice and a declined card are different analytical jobs. Widening one
    prompt to cover both would put "a card that worked last month is not a bad
    card" in front of a model reading an ageing report, which is noise at best."""
    from ...enums import LeakType

    return (RECEIVABLES_PROMPT if record.leak_type is LeakType.OVERDUE_INVOICE
            else SYSTEM_PROMPT)


def signature(record: AtRiskRecord, signal: CohortSignal | None = None) -> str:
    """Identical failures cost one call, not forty. Deliberately coarse: the
    fields below are the ones that actually change the answer."""
    from ...enums import LeakType

    if record.leak_type is LeakType.OVERDUE_INVOICE:
        # Invoices carry no error code, so they need their own key. It has to be
        # COARSE, for the same reason the payments key is: the point of the
        # cache is that identical questions cost one call, and a key built from
        # raw integers like `days_overdue` is unique per record, which is not a
        # cache at all. Signed on the fields that change the ANSWER — has anyone
        # chased it, and how late is it against this buyer's own habit — an
        # invoice 31 days late to a 30-day payer and one 33 days late are the
        # same question and get one call between them.
        rs = record.raw_signals
        overdue = _as_int(rs.get("days_overdue"))
        terms = _as_int(rs.get("payment_terms_days"))
        average = _as_int(rs.get("avg_days_to_pay"))
        inv = [
            "chased" if _as_int(rs.get("reminders_sent")) else "unchased",
            _lateness_band(overdue + terms, average),
            "partial" if rs.get("partial_paid_paise") else "-",
            "disputed" if rs.get("dispute_flag") else "-",
            "po" if rs.get("po_number_present") else "no-po",
            "replied" if rs.get("buyer_reply") else "-",
        ]
        return hashlib.sha256("|".join(inv).encode()).hexdigest()[:16]

    error = record.raw_signals.get("error") or {}
    history = record.raw_signals.get("customer_history") or {}
    parts = [
        str(error.get("code")),
        str(error.get("reason")),
        str(record.raw_signals.get("method")),
        str(record.raw_signals.get("issuer_bank")),
        str(record.raw_signals.get("attempt_number", 1)),
        str(bool(history.get("same_instrument_succeeded_before"))),
        str(bool(signal and signal.indicates_outage)),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _lateness_band(age_days: int, average_days: int) -> str:
    """How late an invoice is, measured against this buyer rather than the
    calendar. Bands rather than a number, because "a fortnight past their usual"
    and "fifteen days past their usual" are one question."""
    if not average_days:
        return "unknown"
    over = age_days - average_days
    if over <= 0:
        return "within-average"
    if over <= 7:
        return "just-over"
    if over <= 21:
        return "well-over"
    return "far-over"


def _days_to_month_end(dt) -> int:
    import calendar

    return calendar.monthrange(dt.year, dt.month)[1] - dt.day


def build_context(record: AtRiskRecord, signal: CohortSignal | None) -> dict:
    from ...enums import LeakType

    if record.leak_type is LeakType.OVERDUE_INVOICE:
        rs = record.raw_signals
        return {
            "amount_paise": record.amount,
            "currency": record.currency,
            "leak_type": record.leak_type.value,
            "issued_at": rs.get("issued_at"),
            "due_at": record.due_at.isoformat() if record.due_at else None,
            "days_overdue": rs.get("days_overdue"),
            "buyer_org": rs.get("buyer_org"),
            "buyer_avg_days_to_pay": rs.get("avg_days_to_pay"),
            "buyer_prior_invoices_paid": rs.get("prior_invoices_paid"),
            "reminders_sent": rs.get("reminders_sent"),
            "partial_paid_paise": rs.get("partial_paid_paise"),
            "dispute_flag": rs.get("dispute_flag"),
            "buyer_reply": rs.get("buyer_reply"),
            "po_number_present": rs.get("po_number_present"),
        }

    ctx: dict[str, Any] = {
        "amount_paise": record.amount,
        "currency": record.currency,
        "leak_type": record.leak_type.value,
        "attempted_at_ist": record.detected_at.isoformat(),
        # Derived, because the prompt asks the model to weigh "late at night"
        # and "near month-end" and an ISO string makes it infer both — including
        # how many days August has. Computing a feature the rule already depends
        # on is the system's job, not the model's; leaving it implicit was
        # costing correct diagnoses on records that did carry the signal.
        "attempted_hour_ist": record.detected_at.hour,
        "days_to_month_end": _days_to_month_end(record.detected_at),
        "attempt_number": record.raw_signals.get("attempt_number", 1),
        "method": record.raw_signals.get("method"),
        "card_network": record.raw_signals.get("card_network"),
        "card_type": record.raw_signals.get("card_type"),
        "issuer_bank": record.raw_signals.get("issuer_bank"),
        "error": record.raw_signals.get("error"),
        "customer_history": record.raw_signals.get("customer_history"),
    }
    if signal is not None:
        ctx["cohort_signal"] = signal.as_dict()
    return ctx
