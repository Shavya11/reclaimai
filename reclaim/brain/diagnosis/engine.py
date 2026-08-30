"""Diagnosis orchestration and its fallback chain.

    cohort signal  ->  layer 1 lookup  ->  layer 2 LLM  ->  UNKNOWN

Every arrow is a downgrade, never a crash. The LLM slot is a callable injected by
the caller so this module stays importable and testable with no API key, and so
`--no-llm` is a real code path rather than a mocked one.
"""

from typing import Callable

from ...enums import LeakType, RootCause
from ...models import AtRiskRecord, Diagnosis
from .cohort import CohortSignal, compute as compute_cohort
from .deterministic import diagnose as diagnose_deterministic

LLMDiagnoser = Callable[[AtRiskRecord, CohortSignal | None], Diagnosis | None]


def _from_cohort(signal: CohortSignal) -> Diagnosis:
    return Diagnosis(
        root_cause=RootCause.BANK_DOWNTIME,
        confidence=0.9,
        reasoning=(
            f"{signal.failures} of {signal.attempts} attempts on {signal.issuer} "
            f"failed in this hour ({signal.failure_rate:.0%}), against a batch "
            f"baseline of {signal.baseline_rate:.1%} — {signal.ratio:.0f}x. The "
            f"error text says 'declined', but the cohort says the issuer is down."
        ),
        recoverable=True,
        evidence_used=[
            f"cohort.failure_rate={signal.failure_rate:.2f}",
            f"cohort.baseline={signal.baseline_rate:.3f}",
            f"cohort.issuer={signal.issuer}",
        ],
        source="cohort",
    )


UNKNOWN_DIAGNOSIS = Diagnosis(
    root_cause=RootCause.UNKNOWN, confidence=0.0,
    reasoning="No layer could identify a cause. Routed to a human by policy.",
    recoverable=False, evidence_used=[], source="fallback",
)


def _before_llm(
    record: AtRiskRecord, signal: CohortSignal | None
) -> Diagnosis | None:
    """Everything the first arrow can answer. None means 'layer 2's turn'."""
    # The cohort outranks the lookup only where the lookup would guess anyway:
    # a card that says "expired" is expired even during an outage.
    if signal is not None and signal.indicates_outage:
        if record.leak_type is not LeakType.ABANDONED_CART:
            if diagnose_deterministic(record) is None:
                return _from_cohort(signal)

    return diagnose_deterministic(record)


def diagnose_one(
    record: AtRiskRecord,
    signal: CohortSignal | None = None,
    llm: LLMDiagnoser | None = None,
) -> Diagnosis:
    found = _before_llm(record, signal)
    if found is not None:
        return found

    if llm is not None:
        try:
            guess = llm(record, signal)
            if guess is not None:
                return guess
        except Exception:  # LLM down -> degrade, never crash the batch
            pass

    return UNKNOWN_DIAGNOSIS.model_copy()


def diagnose_batch(
    records: list[AtRiskRecord],
    traffic: dict[str, int],
    llm: LLMDiagnoser | None = None,
) -> tuple[dict[str, Diagnosis], dict[str, CohortSignal]]:
    """Layer 1 for everyone, then ONE trip to layer 2 for the survivors.

    Asking the model record by record made the arc's runtime a function of how
    many records layer 1 could not resolve, on a free tier that meters requests
    per minute — about forty serialised calls, four minutes of a spinner, and a
    rate limit with no margin left for the retries that then made it worse. The
    survivors are unrelated cases, so nothing is lost by carrying ten of them in
    one request; `many` keeps the per-record fallback for anything the batch
    drops.
    """
    signals = compute_cohort(records, traffic)

    out: dict[str, Diagnosis] = {}
    pending: list[AtRiskRecord] = []
    for record in records:
        found = _before_llm(record, signals.get(record.id))
        if found is not None:
            out[record.id] = found
        else:
            pending.append(record)

    if pending and llm is not None:
        answers = _ask_layer_2(pending, signals, llm)
        for record, answer in zip(pending, answers):
            if answer is not None:
                out[record.id] = answer

    for record in pending:
        out.setdefault(record.id, UNKNOWN_DIAGNOSIS.model_copy())
    return out, signals


def _ask_layer_2(records, signals, llm) -> list[Diagnosis | None]:
    """Batched where the diagnoser supports it, one at a time where it does not.

    `many` is a method on CachedDiagnoser, but `llm` is only ever promised to be
    callable — tests inject bare lambdas and `--no-llm` injects nothing. So the
    batched path is taken when it is offered and the old one still works when it
    is not, which is the same shape as every other degradation in this file.
    """
    calls = [(r, signals.get(r.id)) for r in records]
    many = getattr(llm, "many", None)
    if callable(many):
        try:
            return many(calls)
        except Exception:  # noqa: BLE001 - degrade, never crash the batch
            pass

    answers = []
    for record, signal in calls:
        try:
            answers.append(llm(record, signal))
        except Exception:  # noqa: BLE001
            answers.append(None)
    return answers
