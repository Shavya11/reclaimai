"""Layer 1 and the cohort signal.

The load-bearing test is test_cohort_overrides_a_generic_decline: it is the whole
reason the cohort signal exists, and it is the difference between staying silent
during a bank outage and SMS-blasting fifteen people who were never short.
"""

import pytest

from reclaim.brain.diagnosis.accuracy import cohort_counterfactual, score
from reclaim.brain.diagnosis.cohort import OUTAGE_RATIO, compute as compute_cohort
from reclaim.brain.diagnosis.deterministic import (
    AMBIGUOUS_REASONS, DETERMINISTIC_MAP, coverage, diagnose,
)
from reclaim.brain.diagnosis.engine import diagnose_batch, diagnose_one
from reclaim.enums import LeakType, RootCause
from reclaim.models import AtRiskRecord, Diagnosis
from reclaim.synthetic import generate
from reclaim.timeutil import now


@pytest.fixture(scope="module")
def batch():
    return generate(seed=42)


def _record(reason=None, leak=LeakType.FAILED_PAYMENT, issuer="HDFC", **kw):
    signals = {"issuer_bank": issuer, "method": "card",
               "error": {"reason": reason, "code": "BAD_REQUEST_ERROR"} if reason else None}
    signals.update(kw)
    return AtRiskRecord(id="R1", leak_type=leak, amount=10000,
                        counterparty_id="C1", source_ref="pay_1",
                        detected_at=now(), raw_signals=signals)


# --- layer 1 ----------------------------------------------------------------


def test_every_mapped_value_is_a_real_root_cause():
    for reason, cause in DETERMINISTIC_MAP.items():
        assert isinstance(cause, RootCause), reason


def test_specific_error_resolves_at_full_confidence():
    d = diagnose(_record("card_expired"))
    assert d.root_cause is RootCause.EXPIRED_INSTRUMENT
    assert d.confidence == 1.0
    assert d.source == "deterministic"


def test_ambiguous_error_falls_through_to_layer_two():
    """The generic decline covers four causes needing four different responses.
    Resolving it here would be guessing."""
    for reason in AMBIGUOUS_REASONS:
        assert diagnose(_record(reason)) is None


def test_abandoned_cart_needs_no_error():
    d = diagnose(_record(None, leak=LeakType.ABANDONED_CART))
    assert d.root_cause is RootCause.CART_ABANDONMENT


def test_unrecognised_reason_falls_through():
    assert diagnose(_record("something_we_have_never_seen")) is None


def test_diagnose_never_raises_on_malformed_input():
    junk = AtRiskRecord(id="R", leak_type=LeakType.FAILED_PAYMENT, amount=1,
                        counterparty_id="C", source_ref="s", detected_at=now(),
                        raw_signals={"error": "not-a-dict"})
    assert diagnose(junk) is None


def test_layer_one_coverage_is_near_sixty_percent(batch):
    assert 0.50 <= coverage(batch.records) <= 0.70


# --- cohort signal ----------------------------------------------------------


def test_outage_bucket_is_detected(batch):
    signals = compute_cohort(batch.records, batch.traffic)
    flagged = [r for r in batch.records if signals[r.id].indicates_outage]
    assert len(flagged) >= 10
    assert {signals[r.id].issuer for r in flagged} == {"HDFC"}


def test_outage_signal_exceeds_the_ratio_threshold(batch):
    signals = compute_cohort(batch.records, batch.traffic)
    outage = [s for s in signals.values() if s.indicates_outage]
    assert all(s.ratio >= OUTAGE_RATIO for s in outage)


def test_a_lone_failure_is_not_an_outage():
    """One record on an issuer must never trip the signal."""
    r = _record("payment_failed")
    signals = compute_cohort([r], {})
    assert signals[r.id].indicates_outage is False


def test_cohort_overrides_a_generic_decline(batch):
    """THE load-bearing behaviour: the error says 'declined', the cohort says the
    issuer is down, and the cohort wins."""
    diagnoses, signals = diagnose_batch(batch.records, batch.traffic, llm=None)
    rescued = [r for r in batch.records
               if diagnoses[r.id].source == "cohort"]
    assert len(rescued) >= 10
    for r in rescued:
        assert diagnoses[r.id].root_cause is RootCause.BANK_DOWNTIME
        assert batch.truth[r.id] is RootCause.BANK_DOWNTIME
        assert r.raw_signals["error"]["reason"] in AMBIGUOUS_REASONS


def test_cohort_does_not_override_a_named_error():
    """An expired card is expired even during an outage."""
    from reclaim.brain.diagnosis.cohort import CohortSignal

    outage = CohortSignal(issuer="HDFC", bucket="HDFC|x", failures=40,
                          attempts=50, failure_rate=0.8, baseline_rate=0.04)
    d = diagnose_one(_record("card_expired"), outage, llm=None)
    assert d.root_cause is RootCause.EXPIRED_INSTRUMENT


# --- fallback chain ---------------------------------------------------------


def test_unresolved_records_become_unknown_not_a_crash(batch):
    diagnoses, _ = diagnose_batch(batch.records, batch.traffic, llm=None)
    assert len(diagnoses) == len(batch.records)
    assert any(d.root_cause is RootCause.UNKNOWN for d in diagnoses.values())


def test_a_throwing_llm_degrades_to_unknown(batch):
    def broken(record, signal):
        raise RuntimeError("API down")

    diagnoses, _ = diagnose_batch(batch.records, batch.traffic, llm=broken)
    assert len(diagnoses) == len(batch.records)  # batch still completed


def test_llm_result_is_used_when_layer_one_cannot_answer():
    def stub(record, signal):
        return Diagnosis(root_cause=RootCause.INSUFFICIENT_FUNDS, confidence=0.8,
                         reasoning="stub", recoverable=True, evidence_used=[],
                         source="llm")

    d = diagnose_one(_record("payment_failed"), None, llm=stub)
    assert d.root_cause is RootCause.INSUFFICIENT_FUNDS
    assert d.source == "llm"


# --- measurement ------------------------------------------------------------


def test_deterministic_layer_is_never_wrong(batch):
    """Layer 1 claims confidence 1.0. If it is ever wrong, that claim is a lie."""
    diagnoses, _ = diagnose_batch(batch.records, batch.traffic, llm=None)
    report = score(batch.records, diagnoses, batch.truth)
    assert report.layers["deterministic"].accuracy == 1.0


def test_cohort_layer_is_never_wrong(batch):
    diagnoses, _ = diagnose_batch(batch.records, batch.traffic, llm=None)
    report = score(batch.records, diagnoses, batch.truth)
    assert report.layers["cohort"].accuracy == 1.0


def test_counterfactual_reports_prevented_contacts(batch):
    _, signals = diagnose_batch(batch.records, batch.traffic, llm=None)
    c = cohort_counterfactual(batch.records, signals, batch.truth)
    assert c["needless_contacts_prevented"] >= 10
    assert c["correctly_identified"] == c["records_flagged_as_outage"]
