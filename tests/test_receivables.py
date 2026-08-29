"""B2B receivables: the new leak type, and the promise that adding it changed
nothing underneath.

The most important assertions in this file are the negative ones. V1 claimed its
extension points were real — detectors are plugins, policy is data, the record
stays generic, guardrails sit above the channel. A second leak type is the only
thing that can test that claim, and it either drops in or it does not.
"""

import pytest

from reclaim.brain import rules
from reclaim.brain.diagnosis.deterministic import diagnose
from reclaim.brain.diagnosis.receivables import CLEAR_MARGIN_DAYS
from reclaim.brain.guardrails.rules.value_ceiling import ceiling_for
from reclaim.brain.policy import decide
from reclaim.brain.policy.engine import ladder_step, tone_for
from reclaim.detectors import REGISTRY
from reclaim.enums import (
    ActionType, CAUSES_FOR_LEAK, LeakType, RootCause,
)
from reclaim.models import AtRiskRecord, Diagnosis
from reclaim.synthetic import generate
from reclaim.synthetic.outcomes import BASE_SUCCESS

V1_LEAKS = {LeakType.FAILED_PAYMENT, LeakType.ABANDONED_CART,
            LeakType.FAILED_MANDATE}

RECEIVABLES_CAUSES = {
    RootCause.INVOICE_NOT_RECEIVED, RootCause.INVOICE_DISPUTED,
    RootCause.AWAITING_APPROVAL, RootCause.BUYER_CASH_CRUNCH,
    RootCause.PAYMENT_STALLED,
}


@pytest.fixture(scope="module")
def batch():
    return generate(seed=42)


@pytest.fixture(scope="module")
def invoices(batch):
    return [r for r in batch.records
            if r.leak_type is LeakType.OVERDUE_INVOICE]


# --- the extension points, tested by using them -----------------------------


def test_the_detector_is_one_plugin_and_one_registry_line():
    covered = {d.leak_type for d in REGISTRY}
    assert LeakType.OVERDUE_INVOICE in covered
    assert covered == set(LeakType), "every leak type needs a detector"


def test_the_record_stayed_generic():
    """PROJECT.md §3 said an invoice would be a new leak_type, not a schema
    migration. AtRiskRecord must still carry nothing payment-specific and
    nothing invoice-specific — it all lives in raw_signals."""
    fields = set(AtRiskRecord.model_fields)
    for leaked in ("invoice_id", "days_overdue", "buyer_org", "error",
                   "issuer_bank", "promised_for"):
        assert leaked not in fields


def test_every_receivables_cause_has_a_policy_row_and_an_outcome_rate():
    """CLAUDE.md's four-file rule. A cause missing from either place silently
    skews the batch numbers rather than failing."""
    table = rules.policies()[LeakType.OVERDUE_INVOICE.value]
    for cause in RECEIVABLES_CAUSES:
        assert cause.value in table, f"no policy row for {cause.value}"
        assert cause in BASE_SUCCESS, f"no outcome probability for {cause.value}"


def test_receivables_causes_are_unreachable_for_payments():
    """A card cannot be INVOICE_DISPUTED. Narrowing the enum per leak type is
    what keeps the closed-set guarantee as tight as the domain allows."""
    for cause in RECEIVABLES_CAUSES:
        assert cause not in CAUSES_FOR_LEAK[LeakType.FAILED_PAYMENT]
        assert cause in CAUSES_FOR_LEAK[LeakType.OVERDUE_INVOICE]


# --- layer 1 for receivables ------------------------------------------------


def test_layer_one_resolves_only_what_the_ledger_already_knows(batch, invoices):
    """It must be RIGHT, not broad. A layer claiming confidence 0.85 and above
    that guesses is worse than one that defers — the whole point of deferring is
    that layer 2 and the confidence floor are downstream of it."""
    resolved = correct = 0
    for record in invoices:
        found = diagnose(record)
        if found is None:
            continue
        resolved += 1
        correct += found.root_cause is batch.truth[record.id]

    assert resolved >= 20, "layer 1 should carry a real share of the book"
    assert correct == resolved, "a layer-1 answer must never be wrong"


def test_a_dispute_flag_is_a_fact_not_an_inference(invoices):
    disputed = [r for r in invoices if r.raw_signals.get("dispute_flag")]
    assert disputed
    for record in disputed:
        found = diagnose(record)
        assert found.root_cause is RootCause.INVOICE_DISPUTED
        assert found.confidence == 1.0


def test_a_partial_payment_reads_as_cash_flow_not_dispute(invoices):
    partial = [r for r in invoices
               if r.raw_signals.get("partial_paid_paise")
               and not r.raw_signals.get("dispute_flag")]
    assert partial
    for record in partial:
        assert diagnose(record).root_cause is RootCause.BUYER_CASH_CRUNCH


def test_lateness_is_judged_against_the_buyers_own_average(invoices):
    """A 60-day payer at day 50 is not delinquent. Judging against the due date
    alone would dun the customers who are behaving normally."""
    for record in invoices:
        found = diagnose(record)
        if found is None or found.root_cause is not RootCause.AWAITING_APPROVAL:
            continue
        s = record.raw_signals
        age = s["days_overdue"] + s["payment_terms_days"]
        assert age + CLEAR_MARGIN_DAYS <= s["avg_days_to_pay"]


def test_the_genuinely_ambiguous_pair_is_left_to_layer_two(batch, invoices):
    """An invoice nobody received and one that has stalled look identical from
    the ledger. Layer 1 must not pick between them."""
    for record in invoices:
        found = diagnose(record)
        if found is not None:
            assert found.root_cause not in {RootCause.PAYMENT_STALLED,
                                            RootCause.INVOICE_NOT_RECEIVED}


def test_an_invoice_with_no_signals_at_all_defers_rather_than_crashing():
    bare = AtRiskRecord(
        id="INV_X", leak_type=LeakType.OVERDUE_INVOICE, amount=100_00,
        counterparty_id="B", source_ref="i", detected_at=generate(seed=1)
        .records[0].detected_at, raw_signals={},
    )
    assert diagnose(bare) is None


# --- the dunning ladder -----------------------------------------------------


def _dx(cause):
    return Diagnosis(root_cause=cause, confidence=1.0, reasoning="t",
                     recoverable=True, evidence_used=[], source="deterministic")


def test_the_ladder_escalates_in_tone_and_recipient():
    """§12b's ladder, expressed as data. Day 1 polite, day 7 firmer with a link,
    day 15 with the finance manager copied, day 30 a person."""
    ref = "OVERDUE_INVOICE.PAYMENT_STALLED"
    assert tone_for(ref, 1) == "polite"
    assert tone_for(ref, 2) == "firm"
    assert ladder_step(ref, 3)["cc"] == "finance_manager"
    assert ladder_step(ref, 4)["action"] == "ESCALATE"


def test_the_last_rung_hands_over_to_a_person(batch, invoices):
    """The agent stops. It does not keep climbing and it does not go quiet —
    both of those lose the money differently."""
    record = invoices[0].model_copy(update={"attempts": 3})
    action = decide(record, _dx(RootCause.PAYMENT_STALLED))
    assert action.action_type is ActionType.ESCALATE


def test_a_ladder_shorter_than_its_schedule_degrades_rather_than_crashing():
    """Out of range is empty, not an error: max_attempts already stopped the run
    before here, and a merchant-edited ladder must not be able to kill a batch."""
    assert ladder_step("OVERDUE_INVOICE.PAYMENT_STALLED", 99) == {}
    assert tone_for("OVERDUE_INVOICE.PAYMENT_STALLED", 99) == "neutral"


def test_a_dispute_is_never_auto_actioned(invoices):
    record = next(r for r in invoices if r.raw_signals.get("dispute_flag"))
    action = decide(record, _dx(RootCause.INVOICE_DISPUTED))
    assert action.action_type is ActionType.ESCALATE


# --- bounded authority, per kind of money -----------------------------------


def test_the_value_ceiling_is_higher_for_receivables_than_for_cards():
    """One global number cannot bound authority over a ₹500 card retry and a
    ₹3 lakh invoice at the same time."""
    assert (ceiling_for(LeakType.OVERDUE_INVOICE.value)
            > ceiling_for(LeakType.FAILED_PAYMENT.value))


def test_an_unknown_leak_type_inherits_the_strictest_ceiling():
    """A missing config row must never WIDEN authority."""
    assert (ceiling_for("SOMETHING_ADDED_LATER")
            == ceiling_for(LeakType.FAILED_PAYMENT.value))


def test_the_ceiling_still_fires_on_the_largest_invoices(invoices):
    """Raised, not removed. Bounded authority that nothing ever trips is not a
    bound."""
    ceiling = ceiling_for(LeakType.OVERDUE_INVOICE.value)
    assert any(r.amount > ceiling for r in invoices)


# --- the cohort signal stays a payments concept -----------------------------


def test_invoices_carry_no_issuer_and_join_no_cohort(invoices):
    for record in invoices:
        assert not record.raw_signals.get("issuer_bank")
