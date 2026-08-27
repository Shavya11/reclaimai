"""The batch must be reproducible and must actually contain the conditions the
guardrails and the cohort signal are supposed to find. A generator that never
produces an opted-out customer makes guardrail #2 untestable."""

from collections import Counter

import pytest

from reclaim.brain.diagnosis.deterministic import AMBIGUOUS_REASONS
from reclaim.enums import LeakType, RootCause
from reclaim.synthetic import BASE_SUCCESS, generate, probability
from reclaim.enums import ActionType


@pytest.fixture(scope="module")
def batch():
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


def test_all_three_v1_leak_types_present(batch):
    assert {r.leak_type for r in batch.records} == {
        LeakType.FAILED_PAYMENT, LeakType.ABANDONED_CART, LeakType.FAILED_MANDATE
    }


def test_amounts_within_spec(batch):
    amounts = [r.amount for r in batch.records]
    assert min(amounts) >= 199 * 100
    assert max(amounts) <= 85_000 * 100


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
