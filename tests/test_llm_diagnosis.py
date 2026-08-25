"""Layer 2, driven by a fake Anthropic client.

No API key is needed to run any of this. The properties that matter — a closed
schema, honest validation failure, caching, and graceful degradation — are
properties of our code, not of the model, so they are testable without spending
a token.
"""

import pytest

from reclaim.brain.diagnosis.cohort import CohortSignal
from reclaim.brain.diagnosis.engine import diagnose_batch, diagnose_one
from reclaim.brain.diagnosis.llm_diagnoser import (
    DIAGNOSIS_TOOL,
    SYSTEM_PROMPT,
    LLMDiagnoser,
    build_context,
    signature,
)
from reclaim.enums import LeakType, RootCause
from reclaim.models import AtRiskRecord
from reclaim.synthetic import generate
from reclaim.timeutil import now


class _Block:
    type = "tool_use"

    def __init__(self, payload):
        self.input = payload


class _Response:
    def __init__(self, payload):
        self.content = [_Block(payload)]


class FakeClient:
    """Stands in for anthropic.Anthropic. Records every request it receives."""

    def __init__(self, payload=None, raises=None):
        self.payload = payload or {
            "root_cause": "INSUFFICIENT_FUNDS",
            "confidence": 0.82,
            "reasoning": "Prior successes on this instrument; late-month attempt.",
            "recoverable": True,
            "evidence_used": ["customer_history.same_instrument_succeeded_before"],
        }
        self.raises = raises
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.raises:
            raise self.raises
        return _Response(self.payload)


def _record(reason="payment_failed", **signals):
    base = {
        "issuer_bank": "HDFC",
        "method": "card",
        "attempt_number": 1,
        "error": {"code": "BAD_REQUEST_ERROR", "reason": reason},
        "customer_history": {"same_instrument_succeeded_before": True},
    }
    base.update(signals)
    return AtRiskRecord(
        id="R1", leak_type=LeakType.FAILED_PAYMENT, amount=12400,
        counterparty_id="C1", source_ref="pay_1", detected_at=now(),
        raw_signals=base,
    )


# --- the closed schema ------------------------------------------------------


def test_tool_enum_is_exactly_the_root_cause_enum():
    """The schema is what makes hallucination harmless. If it ever drifts from
    the enum, the model can return something the policy table cannot key on."""
    enum = DIAGNOSIS_TOOL["input_schema"]["properties"]["root_cause"]["enum"]
    assert set(enum) == {c.value for c in RootCause}


def test_all_five_fields_are_required():
    assert set(DIAGNOSIS_TOOL["input_schema"]["required"]) == {
        "root_cause", "confidence", "reasoning", "recoverable", "evidence_used"
    }


def test_tool_use_is_forced_not_offered():
    """tool_choice must pin the tool. Left to `auto`, the model can reply with
    prose and the batch gets nothing it can act on."""
    d = LLMDiagnoser(client=FakeClient())
    d(_record())
    choice = d._client.requests[0]["tool_choice"]
    assert choice == {"type": "tool", "name": "record_diagnosis"}


def test_prompt_licenses_saying_unknown():
    """A model forced to always answer confidently will fabricate."""
    assert "UNKNOWN" in SYSTEM_PROMPT
    assert "do not guess" in SYSTEM_PROMPT.lower()


def test_prompt_forbids_choosing_the_action():
    assert "You do not decide what happens next" in SYSTEM_PROMPT


# --- validation -------------------------------------------------------------


def test_valid_payload_becomes_a_diagnosis():
    d = LLMDiagnoser(client=FakeClient())
    got = d(_record())
    assert got.root_cause is RootCause.INSUFFICIENT_FUNDS
    assert got.source == "llm"
    assert got.confidence == pytest.approx(0.82)


@pytest.mark.parametrize("bad", [
    {"root_cause": "NOT_A_REAL_CAUSE", "confidence": 0.9, "reasoning": "x",
     "recoverable": True, "evidence_used": []},
    {"root_cause": "UNKNOWN", "confidence": 5.0, "reasoning": "x",
     "recoverable": True, "evidence_used": []},
    {"confidence": 0.5, "reasoning": "x", "recoverable": True, "evidence_used": []},
])
def test_invalid_payload_returns_none_rather_than_guessing(bad):
    d = LLMDiagnoser(client=FakeClient(payload=bad))
    assert d(_record()) is None


def test_invalid_payload_becomes_unknown_through_the_engine():
    bad = {"root_cause": "NONSENSE", "confidence": 0.9, "reasoning": "x",
           "recoverable": True, "evidence_used": []}
    d = LLMDiagnoser(client=FakeClient(payload=bad))
    got = diagnose_one(_record(), None, llm=d)
    assert got.root_cause is RootCause.UNKNOWN


# --- cost controls ----------------------------------------------------------


def test_identical_failures_cost_one_call():
    d = LLMDiagnoser(client=FakeClient())
    for _ in range(10):
        d(_record())
    assert d.calls == 1
    assert d.cache_hits == 9


def test_different_failures_are_not_conflated():
    d = LLMDiagnoser(client=FakeClient())
    d(_record(issuer_bank="HDFC"))
    d(_record(issuer_bank="ICICI"))
    assert d.calls == 2


def test_signature_tracks_the_outage_flag():
    """Same error, different cohort verdict, must not share a cache entry —
    that is precisely the case where the right answer differs."""
    outage = CohortSignal("HDFC", "b", 40, 50, 0.8, 0.04)
    calm = CohortSignal("HDFC", "b", 1, 100, 0.01, 0.04)
    assert signature(_record(), outage) != signature(_record(), calm)


def test_context_includes_the_two_signals_that_do_the_work():
    outage = CohortSignal("HDFC", "b", 40, 50, 0.8, 0.04)
    ctx = build_context(_record(), outage)
    assert ctx["customer_history"]["same_instrument_succeeded_before"] is True
    assert ctx["cohort_signal"]["indicates_outage"] is True


# --- degradation ------------------------------------------------------------


def test_api_failure_degrades_to_none_not_an_exception():
    d = LLMDiagnoser(client=FakeClient(raises=RuntimeError("503 upstream")))
    assert d(_record()) is None


def test_batch_completes_when_the_api_is_down():
    """Killing the LLM mid-run is a demo beat. It must degrade, not crash."""
    batch = generate(seed=42)
    d = LLMDiagnoser(client=FakeClient(raises=RuntimeError("connection reset")))
    diagnoses, _ = diagnose_batch(batch.records, batch.traffic, llm=d)
    assert len(diagnoses) == len(batch.records)
    assert all(x is not None for x in diagnoses.values())


def test_no_api_key_means_no_calls_not_a_crash():
    d = LLMDiagnoser(client=None)
    assert d.available is False
    assert d(_record()) is None


def test_llm_never_overrides_the_deterministic_layer():
    """Layer 1 is free and certain. Spending a call to second-guess it would be
    both wasteful and wrong."""
    d = LLMDiagnoser(client=FakeClient())
    got = diagnose_one(_record(reason="card_expired"), None, llm=d)
    assert got.root_cause is RootCause.EXPIRED_INSTRUMENT
    assert got.source == "deterministic"
    assert d.calls == 0
