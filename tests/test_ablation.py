"""The layer-2 ablation, and the checks that stop it flattering us.

The void tests are the important ones. A rate-limited run still completes, still
produces deltas and still attaches a confidence interval to them — so the only
thing standing between a broken run and a published number is a check that
refuses to print. These assert it refuses.
"""

from reclaim.enums import RecordState, RootCause
from reclaim.experiments.ablation import (
    VOID_THRESHOLD, Ablation, Arm, RecordOutcome, _bootstrap_ci,
)


def _outcome(rid, *, recovered=0, contacts=0, queued=False, cause=None,
             state=RecordState.AT_RISK, amount=10_000_00):
    return RecordOutcome(record_id=rid, amount=amount, state=state.value,
                         cause=cause, source="llm", recovered=recovered,
                         contacts=contacts, queued=queued)


def _ablation(with_map, without_map, *, calls=40, failures=0, truth=None):
    return Ablation(
        with_ai=Arm(label="on", outcomes=with_map, calls=calls,
                    failures=failures),
        without_ai=Arm(label="off", outcomes=without_map),
        population=sorted(set(with_map) & set(without_map)),
        truth=truth or {},
        seed=42,
    )


# --- the void checks -------------------------------------------------------


def test_a_run_that_never_called_the_model_is_void():
    """The quiet failure: with no key configured both arms are the same arm,
    every delta is zero, and the report would read as 'layer 2 makes no
    difference' — a conclusion the run did nothing to earn."""
    result = _ablation({"R1": _outcome("R1")}, {"R1": _outcome("R1")}, calls=0)

    assert result.void
    assert "never called" in result.void_reason
    assert result.as_dict()["void"] is True


def test_a_run_with_too_many_unanswered_calls_is_void():
    result = _ablation({"R1": _outcome("R1")}, {"R1": _outcome("R1")},
                       calls=100, failures=40)

    assert result.void
    assert "not an ablation" in result.void_reason


def test_a_healthy_run_is_not_void():
    result = _ablation({"R1": _outcome("R1")}, {"R1": _outcome("R1")},
                       calls=100, failures=5)

    assert not result.void
    assert result.void_reason == ""


def test_the_void_threshold_is_the_boundary():
    ok = _ablation({"R1": _outcome("R1")}, {"R1": _outcome("R1")},
                   calls=100, failures=int(VOID_THRESHOLD * 100))
    bad = _ablation({"R1": _outcome("R1")}, {"R1": _outcome("R1")},
                    calls=100, failures=int(VOID_THRESHOLD * 100) + 1)

    assert not ok.void
    assert bad.void


def test_a_void_run_reports_no_deltas_at_all():
    """Not merely a warning printed above the numbers. A reader who skims takes
    the table, so a void run must not produce one."""
    result = _ablation({"R1": _outcome("R1", recovered=50_000_00)},
                       {"R1": _outcome("R1")}, calls=0)

    payload = result.as_dict()
    assert "recovered_paise" not in payload
    assert "headline" not in payload


# --- the deltas ------------------------------------------------------------


def test_money_recovered_only_counts_the_population_layer_2_answered():
    """Layer 1 resolves most of the batch correctly with or without the model.
    Folding those records in would credit the model with the lookup table."""
    with_ai = {"L2": _outcome("L2", recovered=30_000_00),
               "OTHER": _outcome("OTHER", recovered=90_000_00)}
    without = {"L2": _outcome("L2", recovered=0),
               "OTHER": _outcome("OTHER", recovered=90_000_00)}

    result = Ablation(with_ai=Arm("on", with_ai, calls=10, failures=0),
                      without_ai=Arm("off", without),
                      population=["L2"], truth={}, seed=42)

    assert result.delta(lambda o: o.recovered)["delta"] == 30_000_00


def test_escalations_saved_show_as_a_negative_delta():
    with_ai = {"R1": _outcome("R1", queued=False)}
    without = {"R1": _outcome("R1", queued=True)}

    result = _ablation(with_ai, without)

    assert result.delta(lambda o: 1 if o.queued else 0)["delta"] == -1


# --- the cost side ---------------------------------------------------------


def test_acting_on_a_cause_that_must_never_be_chased_is_reported_as_harm():
    """PROJECT.md already discloses this case: layer 2 reads a RISK_DECLINE as
    INSUFFICIENT_FUNDS and a retry fires against a card the issuer flagged."""
    with_ai = {"R1": _outcome("R1", cause=RootCause.INSUFFICIENT_FUNDS.value,
                              contacts=2)}
    without = {"R1": _outcome("R1", cause=RootCause.UNKNOWN.value)}

    result = _ablation(with_ai, without,
                       truth={"R1": RootCause.RISK_DECLINE})

    harm = result.harmful()
    assert len(harm) == 1
    assert harm[0]["truth"] == "RISK_DECLINE"
    assert harm[0]["diagnosed"] == "INSUFFICIENT_FUNDS"


def test_a_wrong_label_that_produced_no_action_is_not_harm():
    """Being wrong is cheap when nothing fires. The narrower set is the one
    worth reporting, and inflating it would make the cost column meaningless."""
    with_ai = {"R1": _outcome("R1", cause=RootCause.INSUFFICIENT_FUNDS.value,
                              contacts=0)}
    without = {"R1": _outcome("R1", cause=RootCause.UNKNOWN.value)}

    result = _ablation(with_ai, without,
                       truth={"R1": RootCause.RISK_DECLINE})

    assert result.harmful() == []


def test_reading_a_never_chase_cause_correctly_is_not_harm():
    with_ai = {"R1": _outcome("R1", cause=RootCause.RISK_DECLINE.value,
                              contacts=0)}
    without = {"R1": _outcome("R1", cause=RootCause.UNKNOWN.value)}

    result = _ablation(with_ai, without,
                       truth={"R1": RootCause.RISK_DECLINE})

    assert result.harmful() == []


def test_the_headline_states_harm_even_when_the_money_is_positive():
    """A headline that reported only rupees would be an advertisement."""
    with_ai = {"R1": _outcome("R1", recovered=50_000_00,
                              cause=RootCause.INSUFFICIENT_FUNDS.value,
                              contacts=1)}
    without = {"R1": _outcome("R1")}

    result = _ablation(with_ai, without,
                       truth={"R1": RootCause.RISK_DECLINE})

    assert "1 harmful action" in result.headline()


# --- the interval ----------------------------------------------------------


def test_the_interval_is_reproducible():
    pairs = [(1.0, 0.0), (2.0, 1.0), (0.0, 0.0), (5.0, 2.0)]
    assert _bootstrap_ci(pairs) == _bootstrap_ci(pairs)


def test_an_all_zero_difference_gives_an_interval_containing_zero():
    lo, hi = _bootstrap_ci([(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)])
    assert lo <= 0 <= hi


def test_a_consistent_difference_gives_an_interval_excluding_zero():
    lo, hi = _bootstrap_ci([(5.0, 1.0)] * 40)
    assert lo > 0


def test_an_empty_population_does_not_raise():
    assert _bootstrap_ci([]) == (0.0, 0.0)
