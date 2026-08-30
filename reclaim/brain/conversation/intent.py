"""The model's second job: read what a customer said back.

It is the same job as the first, and deliberately so. The model receives a
sentence and returns a member of a closed enum plus a confidence. It does not
decide what the label means, what happens next, who gets contacted, or when —
`handler.py` holds the deterministic table for that, exactly as `policy/` does
for RootCause.

One field needs saying out loud. The model may extract a DATE from "we'll pay by
Friday", because pulling a date out of a sentence is what a language model is
for. It is not trusted with that date: `promises.validate_date` refuses anything
in the past, anything past the configured horizon, and anything unparseable, and
a refused date becomes a reply a human reads rather than a record the agent puts
to sleep. The model reads; the deterministic code decides. That is the rule
about money, applied to time — and in receivables, time is how the money gets
away.
"""

import hashlib
import json
import logging
from typing import Any

from pydantic import ValidationError

from ...config import settings
from ...enums import ReplyIntent
from ...models import ReplyReading
from ..diagnosis.llm_diagnoser import (
    BATCH_INSTRUCTION,
    MAX_TOKENS,
    CachedDiagnoser,
    batch_max_tokens,
    batch_tool,
    unpack_batch,
)

log = logging.getLogger(__name__)

INTENT_TOOL: dict[str, Any] = {
    "name": "record_reply_intent",
    "description": (
        "Record what a customer or accounts-payable contact said in reply to a "
        "payment or invoice reminder, as one intent from a closed list."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": [i.value for i in ReplyIntent],
                "description": "Exactly one intent from the closed list.",
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Below 0.6 routes the reply to a human. Be honest.",
            },
            "promised_date": {
                "type": ["string", "null"],
                "description": (
                    "ISO date (YYYY-MM-DD) ONLY for PROMISE_TO_PAY, and only "
                    "when the reply names or clearly implies one. Null "
                    "otherwise. Resolve relative dates such as a weekday name "
                    "or 'next week' against the date given as `today`."
                ),
            },
            "reasoning": {
                "type": "string",
                "description": "One sentence a collections agent could read.",
            },
            "quote": {
                "type": "string",
                "description": "The words in the reply that carried the intent.",
            },
        },
        "required": ["intent", "confidence", "reasoning", "quote"],
    },
}

SYSTEM_PROMPT = """You read replies to payment and invoice reminders for an Indian
merchant, and classify each into exactly one intent.

Replies are short, informal, and frequently Hinglish - Hindi written in Latin
script, mixed with English. Read them as an Indian collections agent would:

- "ho jayega" / "kar dunga" / "bhej dunga" - it will be done. A commitment.
- "abhi nahi" / "paise nahi hai" - not now, no funds. Not a refusal to pay.
- "kar diya" / "ho gaya" / "already transferred" - they believe they have paid.
- "galat number" / "main nahi hoon" - wrong person.
- "mat bhejo" / "band karo" / "stop sending" - stop contacting them.

Rules, in order of precedence:

- STOP_CONTACTING outranks everything. If the reply asks to stop being
  contacted, that is the intent, even when it also contains a promise. Somebody
  who says "I'll pay, now stop messaging me" has withdrawn consent, and consent
  does not lose to a collection.
- ALREADY_PAID whenever they claim payment was made. It is not a promise; it is
  a claim about the past, and it needs reconciling rather than chasing.
- PROMISE_TO_PAY only for a commitment to pay in FULL, on or by an identifiable
  date. "I'll pay something next month" is PARTIAL_PAYMENT_OFFER. "I'll pay when
  I can" names no date and commits to nothing - that is UNCLEAR.
- PARTIAL_PAYMENT_OFFER when they offer less than the full amount, or
  instalments. That is a commercial negotiation and a person must handle it.
- DISPUTED when they contest the amount, the goods, the terms, or say they never
  ordered it - including a missing purchase order or an owed credit note.
- WRONG_CONTACT when they say they are not the right person, or have left the
  company, or do not recognise the debt at all.
- UNCLEAR when the reply is empty, off-topic, ambiguous between two intents, or
  a question rather than an answer. UNCLEAR reaches a person, which is the
  correct outcome for something you did not understand. Do not guess an intent
  in order to look useful.

For PROMISE_TO_PAY, resolve the date against `today` in the context. A weekday
name means the NEXT such weekday. If no date can be identified, still return
PROMISE_TO_PAY with a null date - the system routes it to a human rather than
inventing one.

You are producing a label only. You do not decide what happens next."""


def _validate(payload: dict) -> ReplyReading | None:
    """A schema violation is not a crash and not a guess. It is an UNCLEAR that
    a human picks up."""
    try:
        return ReplyReading(
            intent=ReplyIntent(payload["intent"]),
            confidence=float(payload["confidence"]),
            reasoning=str(payload["reasoning"]),
            quote=str(payload.get("quote", ""))[:400],
            promised_date=(str(payload["promised_date"])
                           if payload.get("promised_date") else None),
            source="llm",
        )
    except (KeyError, ValueError, TypeError, ValidationError) as exc:
        log.warning("reply extractor returned an unusable payload: %s", exc)
        return None


BATCH_INTENT_TOOL: dict[str, Any] = batch_tool(INTENT_TOOL)


def build_batch_context(calls) -> dict:
    replies = []
    for i, call in enumerate(calls):
        reply = call[0]
        today = call[1] if len(call) > 1 else None
        record = call[2] if len(call) > 2 else None
        replies.append({"index": i, **build_context(reply, today=today, record=record)})
    return {"replies": replies}


def build_context(reply: str, *, today, record=None) -> dict:
    ctx: dict[str, Any] = {"reply": reply, "today": today.strftime("%Y-%m-%d")}
    if record is not None:
        ctx["amount_paise"] = record.amount
        ctx["leak_type"] = record.leak_type.value
        ctx["days_overdue"] = record.raw_signals.get("days_overdue")
        ctx["buyer_org"] = record.raw_signals.get("buyer_org")
    return ctx


class IntentExtractor(CachedDiagnoser):
    """Anthropic. Inherits caching, the concurrency cap and never-raise from
    CachedDiagnoser - the same guarantees layer 2 runs under, because a reply
    that cannot be read must degrade to a human rather than stop a batch."""

    def __init__(self, client=None, model: str | None = None) -> None:
        super().__init__(client, model or settings.anthropic_model)
        if self._client is None and settings.has_anthropic:
            import anthropic

            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def _signature(self, reply, today=None, record=None) -> str:
        """Keyed on the words alone.

        Two buyers sending "friday tak ho jayega" are one call, which is what
        makes this affordable on a free tier. The date they resolve against is
        part of the key because it changes the answer, and it is the same value
        for a whole batch, so it costs nothing.
        """
        return hashlib.sha256(
            f"{str(reply).strip().lower()}|{today}".encode()
        ).hexdigest()[:16]

    def _ask(self, reply, today=None, record=None) -> ReplyReading | None:
        self.calls += 1
        response = self._client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=[INTENT_TOOL],
            tool_choice={"type": "tool", "name": INTENT_TOOL["name"]},
            messages=[{"role": "user", "content": json.dumps(
                build_context(reply, today=today, record=record), indent=2)}],
        )
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                return _validate(block.input)
        return None

    def _ask_batch(self, calls):
        self.calls += 1
        response = self._client.messages.create(
            model=self.model,
            max_tokens=batch_max_tokens(len(calls)),
            system=SYSTEM_PROMPT + BATCH_INSTRUCTION,
            tools=[BATCH_INTENT_TOOL],
            tool_choice={"type": "tool", "name": BATCH_INTENT_TOOL["name"]},
            messages=[{"role": "user", "content": json.dumps(
                build_batch_context(calls), indent=2)}],
        )
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                return unpack_batch(block.input, len(calls), _validate)
        return None


class GeminiIntentExtractor(IntentExtractor):
    """Same prompt, same closed schema, same validation - different vendor.

    Subclassed off the Anthropic extractor rather than written again, for the
    reason CLAUDE.md gives: two copies of a rule about what the model may decide
    is one copy that goes stale.
    """

    def __init__(self, client=None, model: str | None = None) -> None:
        CachedDiagnoser.__init__(self, client, model or settings.gemini_model)
        self._throttled = client is None
        if self._client is None and settings.has_gemini:
            from google import genai

            self._client = genai.Client(api_key=settings.gemini_api_key)

    def _ask(self, reply, today=None, record=None) -> ReplyReading | None:
        from google.genai import types

        from ..diagnosis.gemini_diagnoser import GeminiDiagnoser

        self.calls += 1
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=MAX_TOKENS,
            tools=[types.Tool(function_declarations=[types.FunctionDeclaration(
                name=INTENT_TOOL["name"],
                description=INTENT_TOOL["description"],
                parameters_json_schema=INTENT_TOOL["input_schema"],
            )])],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode="ANY", allowed_function_names=[INTENT_TOOL["name"]])),
        )
        # The free tier's per-minute cap is shared across every job we run
        # against it, so the reply extractor paces through the same gate the
        # diagnoser does rather than keeping a budget of its own.
        if self._throttled:
            GeminiDiagnoser._pace()
        response = self._client.models.generate_content(
            model=self.model,
            contents=json.dumps(build_context(reply, today=today, record=record)),
            config=config,
        )
        for call in response.function_calls or []:
            if call.name == INTENT_TOOL["name"]:
                return _validate(dict(call.args or {}))
        return None

    def _ask_batch(self, calls):
        """A tick's replies in one request. They pace through the same gate the
        diagnoser does, so every one of them saved four seconds off the arc."""
        from ..diagnosis.gemini_diagnoser import GeminiDiagnoser, _tool_config

        self.calls += 1
        config = _tool_config(BATCH_INTENT_TOOL, SYSTEM_PROMPT + BATCH_INSTRUCTION,
                              batch_max_tokens(len(calls)))
        if self._throttled:
            GeminiDiagnoser._pace()
        response = self._client.models.generate_content(
            model=self.model,
            contents=json.dumps(build_batch_context(calls), indent=2),
            config=config,
        )
        for call in response.function_calls or []:
            if call.name == BATCH_INTENT_TOOL["name"]:
                return unpack_batch(dict(call.args or {}), len(calls), _validate)
        return None


def build_extractor(enabled: bool = True):
    """Anthropic when keyed, Gemini otherwise, None when neither - the same
    precedence as layer 2, because that is what PROJECT.md describes."""
    if not enabled:
        return None
    if settings.has_anthropic:
        return IntentExtractor()
    if settings.has_gemini:
        return GeminiIntentExtractor()
    return None


# --- the fallback, which is deliberately not the model ----------------------

_KEYWORDS: list[tuple[ReplyIntent, tuple[str, ...]]] = [
    (ReplyIntent.STOP_CONTACTING,
     ("stop", "unsubscribe", "mat bhejo", "band karo", "do not contact",
      "remove me", "pareshan")),
    (ReplyIntent.ALREADY_PAID,
     ("already paid", "kar diya", "ho gaya", "transferred", "utr", "paid on",
      "payment done", "bhej diya")),
    (ReplyIntent.DISPUTED,
     ("dispute", "wrong amount", "galat amount", "credit note", "never ordered",
      "short supply", "damaged", "po number", "mismatch")),
    (ReplyIntent.WRONG_CONTACT,
     ("wrong number", "galat number", "not me", "no longer with",
      "left the company", "main nahi")),
    (ReplyIntent.PARTIAL_PAYMENT_OFFER,
     ("part payment", "partial", "instal", "emi", "half now")),
    (ReplyIntent.PROMISE_TO_PAY,
     ("ho jayega", "kar dunga", "bhej dunga", "will pay", "clear kar",
      "release")),
]


def keyword_reading(reply: str) -> ReplyReading:
    """What happens when there is no model, or the model declined.

    Deliberately weak, and deliberately honest about being weak: confidence 0.5
    sits below the 0.6 floor, so a keyword match never acts on its own - it
    labels the reply for a person rather than pretending to have understood it.
    The point is that the batch always completes, not that a substring match is
    good enough to move somebody's money.
    """
    text = (reply or "").lower()
    for intent, needles in _KEYWORDS:
        for needle in needles:
            if needle in text:
                return ReplyReading(
                    intent=intent, confidence=0.5,
                    reasoning=f"Matched the phrase {needle!r} with no model "
                              f"available. Below the confidence floor by "
                              f"design, so a person confirms it.",
                    quote=needle, promised_date=None, source="fallback",
                )
    return ReplyReading(
        intent=ReplyIntent.UNCLEAR, confidence=0.0,
        reasoning="No model available and no recognisable phrase.",
        quote="", promised_date=None, source="fallback",
    )
