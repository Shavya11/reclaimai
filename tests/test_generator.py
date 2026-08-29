"""The batch must be reproducible and must actually contain the conditions the
guardrails and the cohort signal are supposed to find. A generator that never
produces an opted-out customer makes guardrail #2 untestable."""

from collections import Counter

import pytest

from reclaim.brain.diagnosis.deterministic import AMBIGUOUS_REASONS
from reclaim.enums import LeakType, RootCause
from reclaim.synthetic import BASE_SUCCESS, generate, probability
from reclaim.enums import ActionType


V1_LEAKS = {LeakType.FAILED_PAYMENT, LeakType.ABANDONED_CART,
            LeakType.FAILED_MANDATE}


@pytest.fixture(scope="module")
def batch():
    """The payments half.

    Scoped deliberately. Most assertions in this file are about consumer
    payments — ₹199-₹85,000 tickets, an error payload on every record, a ~40%
    ambiguous share — and none of them are true of a B2B invoice, which has no
    error code and can be ₹12 lakh. Widening them to the whole batch would not
    make them stronger, it would make them vacuous."""
    return generate(seed=42, leak_types=V1_LEAKS)


@pytest.fixture(scope="module")
def full():
    return generate(seed=42)


def test_same_seed_same_batch():
    a, b = generate(seed=42), generate(seed=42)
    assert [r.id for r in a.records] == [r.id for r in b.records]
    assert a.total_at_risk == b.total_at_risk
    assert [r.amount for r in a.records] == [r.amount for r in b.records]


def test_different_seed_different_batch():
    assert generate(seed=7).total_at_risk != generate(seed=42).total_at_risk


def test_batch_size(batch):
    assert len(batch.records) == 120


def test_full_batch_adds_receivables_without_disturbing_payments(full):
    """The V2 invariant this whole design rests on.

    Invoices are drawn from their own RNG stream, appended after the payments
    batch. If that ever stops being true, every figure PROJECT.md and the README
    publish becomes wrong silently — the batch still generates, still runs, and
    still looks right. Hence an exact identity check, not a size check."""
    v1 = generate(seed=42, leak_types=V1_LEAKS)
    payments = [r for r in full.records if r.leak_type in V1_LEAKS]

    assert len(full.records) == 180
    assert [r.id for r in payments] == [r.id for r in v1.records]
    assert [r.amount for r in payments] == [r.amount for r in v1.records]
    assert [r.detected_at for r in payments] == [r.detected_at for r in v1.records]
    assert sum(r.amount for r in payments) == 82_498_400


def test_all_three_v1_leak_types_present(batch):
    assert {r.leak_type for r in batch.records} == V1_LEAKS


def test_the_filter_returns_a_subset_not_a_different_batch(full):
    """`--leak-types` must narrow the batch, never regenerate it. Filtering
    before the draws instead of after would give a different fixture the same
    name."""
    only_invoices = generate(seed=42, leak_types={LeakType.OVERDUE_INVOICE})
    from_full = [r for r in full.records
                 if r.leak_type is LeakType.OVERDUE_INVOICE]
    assert [r.id for r in only_invoices.records] == [r.id for r in from_full]
    assert [r.amount for r in only_invoices.records] == [r.amount for r in from_full]


def test_amounts_within_spec(batch):
    amounts = [r.amount for r in batch.records]
    assert min(amounts) >= 199 * 100
    assert max(amounts) <= 85_000 * 100


def test_invoice_amounts_are_b2b_scale(full):
    """B2B tickets are an order of magnitude above consumer ones. That is not
    cosmetic: it is what makes the value ceiling fire on receivables and what
    gives DSO room to move."""
    inv = [r.amount for r in full.records
           if r.leak_type is LeakType.OVERDUE_INVOICE]
    assert len(inv) == 60
    assert min(inv) >= 20_000 * 100
    assert max(inv) > 50_000 * 100
    assert sum(1 for a in inv if a > 50_000 * 100) >= 25


def test_value_ceiling_has_something_to_fire_on(batch):
    """Guardrail #8 blocks above ₹50,000. Without these it never runs."""
    assert sum(1 for r in batch.records if r.amount > 50_000 * 100) >= 5


def test_opt_out_and_dnd_populations_exist(batch):
    assert any(c.opted_out for c in batch.customers)
    assert any(c.on_dnd for c in batch.customers)


def test_opted_out_customers_actually_receive_contact_attempts(batch):
    """Guardrail #2 is demo beat #4. Flags assigned at random leave it to chance
    whether consent ever fires — with 3 opted-out customers in 55 it usually
    does not, and a guardrail that never fires cannot be demonstrated."""
    from reclaim.synthetic.generator import CONTACTING_CAUSES

    opted = {c.id for c in batch.customers if c.opted_out}
    reachable = {r.counterparty_id for r in batch.records
                 if batch.truth[r.id] in CONTACTING_CAUSES}
    assert len(opted & reachable) >= 3


def test_dnd_customers_actually_receive_contact_attempts(batch):
    from reclaim.synthetic.generator import CONTACTING_CAUSES

    dnd = {c.id for c in batch.customers if c.on_dnd}
    reachable = {r.counterparty_id for r in batch.records
                 if batch.truth[r.id] in CONTACTING_CAUSES}
    assert len(dnd & reachable) >= 3


def test_opt_out_and_dnd_are_disjoint(batch):
    assert not any(c.opted_out and c.on_dnd for c in batch.customers)


def test_frequency_cap_has_something_to_fire_on(batch):
    """Guardrail #7 is customer-level. It is only meaningful if some customers
    own several records."""
    counts = Counter(r.counterparty_id for r in batch.records)
    assert max(counts.values()) >= 3


def test_issuer_outage_cluster_exists(batch):
    """~15 failures on one issuer inside one hour, carrying a generic decline —
    so only the cohort signal can identify them as BANK_DOWNTIME."""
    hdfc = [r for r in batch.records if r.id in batch.outage_ids]
    assert len(hdfc) >= 10
    window = {r.detected_at.replace(minute=0, second=0, microsecond=0) for r in hdfc}
    assert len(window) <= 2  # all inside one hour bucket


def test_clustered_outage_records_look_like_plain_declines(batch):
    """If the error text gave it away, the cohort signal would be decoration."""
    clustered = [r for r in batch.records if r.id in batch.outage_ids]
    for r in clustered:
        assert r.raw_signals["error"]["reason"] in AMBIGUOUS_REASONS


def test_ambiguous_share_is_roughly_forty_percent(batch):
    """PROJECT.md §5: the deterministic layer should resolve ~60%, leaving ~40%
    where the LLM earns its place."""
    amb = sum(1 for r in batch.records
              if (r.raw_signals.get("error") or {}).get("reason")
              in AMBIGUOUS_REASONS)
    assert 0.30 <= amb / len(batch.records) <= 0.50


def test_every_invoice_carries_the_signals_the_prompt_asks_for(full):
    """V1's hardest lesson, applied before it can cost anything this time: a
    fixture that labels a record and then withholds the evidence for that label
    scores the model zero for being honest. Every field the receivables prompt
    names must be present on every invoice."""
    required = {"days_overdue", "avg_days_to_pay", "prior_invoices_paid",
                "reminders_sent", "partial_paid_paise", "dispute_flag",
                "po_number_present", "buyer_org", "payment_terms_days"}
    invoices = [r for r in full.records
                if r.leak_type is LeakType.OVERDUE_INVOICE]
    assert invoices
    for r in invoices:
        missing = required - set(r.raw_signals)
        assert not missing, f"{r.id} missing {missing}"


def test_the_causes_that_need_a_tell_have_one(full):
    """Each receivables cause is distinguishable from the others by the fields
    the prompt is told to read. If two causes look identical in the data, the
    honest answer is UNKNOWN and layer 2 scores zero through no fault of its
    own."""
    by_cause = {}
    for r in full.records:
        if r.leak_type is LeakType.OVERDUE_INVOICE:
            by_cause.setdefault(full.truth[r.id], []).append(r.raw_signals)

    assert all(s["partial_paid_paise"] > 0
               for s in by_cause[RootCause.BUYER_CASH_CRUNCH])
    assert all(s["dispute_flag"] for s in by_cause[RootCause.INVOICE_DISPUTED])
    assert all(s["reminders_sent"] == 0
               for s in by_cause[RootCause.INVOICE_NOT_RECEIVED])
    # An approval-cycle invoice is late by the calendar but not by this buyer's
    # own habit. That gap is the entire tell.
    assert all(s["avg_days_to_pay"] > s["days_overdue"] + s["payment_terms_days"]
               for s in by_cause[RootCause.AWAITING_APPROVAL])


def test_abandoned_carts_have_no_error_payload(batch):
    for r in batch.records:
        if r.leak_type is LeakType.ABANDONED_CART:
            assert r.raw_signals["error"] is None


# --- outcome simulator ------------------------------------------------------


def test_every_root_cause_has_a_success_probability():
    assert set(BASE_SUCCESS) == set(RootCause)


def test_unrecoverable_causes_are_zero():
    for c in (RootCause.RISK_DECLINE, RootCause.MANDATE_REVOKED,
              RootCause.POLICY_BLOCK, RootCause.UNKNOWN):
        assert BASE_SUCCESS[c] == 0.0


def test_salary_timing_beats_immediate_retry():
    """The headline measurable claim: 12% -> 41%."""
    immediate = probability(RootCause.INSUFFICIENT_FUNDS, action=ActionType.RETRY)
    salary = probability(RootCause.INSUFFICIENT_FUNDS, action=ActionType.RETRY,
                         in_salary_window=True)
    assert salary > immediate * 3


def test_no_action_never_recovers():
    for c in RootCause:
        assert probability(c, action=ActionType.NO_ACTION) == 0.0


def test_later_attempts_are_worth_less():
    a1 = probability(RootCause.BANK_DOWNTIME, action=ActionType.SILENT_RETRY, attempt_number=1)
    a3 = probability(RootCause.BANK_DOWNTIME, action=ActionType.SILENT_RETRY, attempt_number=3)
    assert a3 < a1
