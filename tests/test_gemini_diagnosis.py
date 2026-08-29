"""Layer 2 on Gemini, driven by a fake client.

Same reasoning as `test_llm_diagnosis.py`: the properties worth testing — a
forced call into a closed schema, honest validation failure, caching, graceful
degradation — belong to our code, not the vendor's, so none of this spends a
token or needs a key.

What these tests exist to catch specifically is drift *between* the two
providers. A guarantee that holds on Anthropic and quietly does not hold on
Gemini is worse than no second provider at all.
"""

import pytest

from reclaim.brain.diagnosis.engine import diagnose_one
from reclaim.brain.diagnosis.gemini_diagnoser import TOOL_NAME, GeminiDiagnoser
from reclaim.brain.diagnosis.llm_diagnoser import (
    DIAGNOSIS_TOOL, SYSTEM_PROMPT, prompt_for, tool_for,
)
from reclaim.enums import LeakType, RootCause
from reclaim.models import AtRiskRecord
from reclaim.timeutil import now

VALID = {
    "root_cause": "INSUFFICIENT_FUNDS",
    "confidence": 0.82,
    "reasoning": "Prior successes on this instrument; late-month attempt.",
    "recoverable": True,
    "evidence_used": ["customer_history.same_instrument_succeeded_before"],
}


class _Call:
    def __init__(self, payload, name=TOOL_NAME):
        self.name = name
        self.args = payload


class _Response:
    def __init__(self, calls):
        self.function_calls = calls


class FakeModels:
    def __init__(self, outer):
        self._outer = outer

    def generate_content(self, **kwargs):
        self._outer.requests.append(kwargs)
        if self._outer.raises:
            raise self._outer.raises
        return _Response(self._outer.calls)


class FakeClient:
    """Stands in for google.genai.Client."""

    def __init__(self, payload=None, raises=None, calls=None, name=TOOL_NAME):
        self.raises = raises
        self.requests = []
        self.calls = (
            calls if calls is not None
            else [_Call(payload or dict(VALID), name)]
        )
        self.models = FakeModels(self)


def _record(reason="payment_failed", leak_type=LeakType.FAILED_PAYMENT):
    return AtRiskRecord(
        id="R1", leak_type=leak_type, amount=12400,
        counterparty_id="C1", source_ref="pay_1", detected_at=now(),
        raw_signals={
            "issuer_bank": "HDFC", "method": "card", "attempt_number": 1,
            "error": {"code": "BAD_REQUEST_ERROR", "reason": reason},
            "customer_history": {"same_instrument_succeeded_before": True},
        },
    )


# --- the forced call --------------------------------------------------------


def test_the_call_is_forced_not_offered():
    """Mode ANY plus allowed_function_names is Gemini's equivalent of
    tool_choice. Without it the model may answer in prose, and prose is exactly
    what the closed enum exists to prevent."""
    client = FakeClient()
    GeminiDiagnoser(client=client, model="m")(_record())
    cfg = client.requests[0]["config"]
    fcc = cfg.tool_config.function_calling_config
    assert fcc.mode == "ANY"
    assert fcc.allowed_function_names == [TOOL_NAME]


def test_schema_sent_to_gemini_is_the_same_closed_schema():
    """One schema, two providers. If these drift, one of them is letting the
    model invent a root cause.

    Compared per record rather than against the module constant, because the
    enum is narrowed to the causes the record's leak type can actually have.
    Both providers must narrow it identically — a provider that skipped the
    narrowing would be offering causes the policy table has no row for."""
    record = _record()
    client = FakeClient()
    GeminiDiagnoser(client=client, model="m")(record)
    decl = client.requests[0]["config"].tools[0].function_declarations[0]
    assert decl.name == DIAGNOSIS_TOOL["name"]
    assert decl.parameters_json_schema == tool_for(record)["input_schema"]


def test_the_offered_enum_is_always_a_subset_of_the_closed_set():
    """Narrowing may only ever remove members. A leak type that somehow added
    one would be a cause with no policy row and no outcome probability."""
    every = set(DIAGNOSIS_TOOL["input_schema"]["properties"]["root_cause"]["enum"])
    for leak in LeakType:
        offered = set(
            tool_for(_record(leak_type=leak))
            ["input_schema"]["properties"]["root_cause"]["enum"]
        )
        assert offered and offered <= every, leak


def test_same_system_prompt_as_the_anthropic_path():
    record = _record()
    client = FakeClient()
    GeminiDiagnoser(client=client, model="m")(record)
    assert client.requests[0]["config"].system_instruction == prompt_for(record)


# --- validation and degradation ---------------------------------------------


def test_valid_payload_becomes_a_diagnosis():
    d = GeminiDiagnoser(client=FakeClient(), model="m")(_record())
    assert d.root_cause is RootCause.INSUFFICIENT_FUNDS
    assert d.source == "llm"


@pytest.mark.parametrize("bad", [
    {**VALID, "root_cause": "SOMETHING_INVENTED"},
    {**VALID, "confidence": "very sure"},
    {k: v for k, v in VALID.items() if k != "reasoning"},
])
def test_invalid_payload_returns_none_rather_than_guessing(bad):
    assert GeminiDiagnoser(client=FakeClient(payload=bad), model="m")(_record()) is None


def test_a_call_to_the_wrong_tool_is_ignored():
    client = FakeClient(name="something_else")
    assert GeminiDiagnoser(client=client, model="m")(_record()) is None


def test_no_function_call_at_all_is_none_not_a_crash():
    assert GeminiDiagnoser(client=FakeClient(calls=[]), model="m")(_record()) is None


def test_api_failure_degrades_to_none_not_an_exception():
    client = FakeClient(raises=RuntimeError("503"))
    assert GeminiDiagnoser(client=client, model="m")(_record()) is None


def test_invalid_payload_becomes_unknown_through_the_engine():
    """The contract the rest of the system relies on: layer 2 failing is an
    UNKNOWN bound for a human, never a hole in the batch."""
    bad = {**VALID, "root_cause": "SOMETHING_INVENTED"}
    d = diagnose_one(_record(), None, GeminiDiagnoser(client=FakeClient(payload=bad),
                                                      model="m"))
    assert d.root_cause is RootCause.UNKNOWN


def test_no_api_key_means_no_calls_not_a_crash():
    g = GeminiDiagnoser(client=None, model="m")
    assert g.available is False
    assert g(_record()) is None


# --- the cache, inherited from CachedDiagnoser ------------------------------


def test_identical_failures_cost_one_call():
    client = FakeClient()
    g = GeminiDiagnoser(client=client, model="m")
    for _ in range(5):
        g(_record())
    assert len(client.requests) == 1
    assert g.cache_hits == 4


def test_different_failures_are_not_conflated():
    client = FakeClient()
    g = GeminiDiagnoser(client=client, model="m")
    g(_record("payment_failed"))
    g(_record("card_declined"))
    assert len(client.requests) == 2
