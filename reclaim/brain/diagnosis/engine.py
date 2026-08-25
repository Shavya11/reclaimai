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


def diagnose_one(
    record: AtRiskRecord,
    signal: CohortSignal | None = None,
    llm: LLMDiagnoser | None = None,
) -> Diagnosis:
    # The cohort outranks the lookup only where the lookup would guess anyway:
    # a card that says "expired" is expired even during an outage.
    if signal is not None and signal.indicates_outage:
        if record.leak_type is not LeakType.ABANDONED_CART:
            if diagnose_deterministic(record) is None:
                return _from_cohort(signal)

    found = diagnose_deterministic(record)
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
    signals = compute_cohort(records, traffic)
    return ({r.id: diagnose_one(r, signals.get(r.id), llm) for r in records},
            signals)
