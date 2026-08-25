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
- A late-night attempt near month-end, on an instrument with prior successes,
  weakly suggests INSUFFICIENT_FUNDS rather than a broken card.
- Set confidence below 0.6 whenever you are genuinely unsure. That routes the
  record to human review, which is the correct outcome for an ambiguous case.

You are producing a label only. You do not decide what happens next."""


class LLMDiagnoser:
    def __init__(self, client=None, model: str | None = None) -> None:
        self.model = model or settings.anthropic_model
        self._semaphore = threading.Semaphore(MAX_CONCURRENCY)
        self._cache: dict[str, Diagnosis] = {}
        self._lock = threading.Lock()
        self.calls = 0
        self.cache_hits = 0
        self._client = client
        if self._client is None and settings.has_anthropic:
            import anthropic

            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    @property
    def available(self) -> bool:
        return self._client is not None

    def __call__(
        self, record: AtRiskRecord, signal: CohortSignal | None = None
    ) -> Diagnosis | None:
        """Matches the LLMDiagnoser protocol the engine injects. Returns None so
        the caller falls through to UNKNOWN. Never raises, never blocks a batch."""
        if not self.available:
            return None

        key = signature(record, signal)
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self.cache_hits += 1
                return hit.model_copy()

        try:
            with self._semaphore:
                diagnosis = self._ask(record, signal)
        except Exception as exc:  # noqa: BLE001 - API down must not kill the batch
            log.warning("LLM diagnosis failed for %s: %s", record.id, exc)
            return None

        if diagnosis is not None:
            with self._lock:
                self._cache[key] = diagnosis
        return diagnosis

    def _ask(self, record, signal) -> Diagnosis | None:
        self.calls += 1
        response = self._client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=[DIAGNOSIS_TOOL],
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


def signature(record: AtRiskRecord, signal: CohortSignal | None) -> str:
    """Identical failures cost one call, not forty. Deliberately coarse: the
    fields below are the ones that actually change the answer."""
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


def build_context(record: AtRiskRecord, signal: CohortSignal | None) -> dict:
    ctx: dict[str, Any] = {
        "amount_paise": record.amount,
        "currency": record.currency,
        "leak_type": record.leak_type.value,
        "attempted_at_ist": record.detected_at.isoformat(),
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
