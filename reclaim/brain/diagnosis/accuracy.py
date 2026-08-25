"""Scores diagnosis against ground truth.

The synthetic generator records the cause it planted in every record, so accuracy
here is measured, not asserted. Most projects cannot do this: they show a model
output and ask you to believe it.

It also answers the counterfactual that justifies the cohort signal existing —
how many customers would have been contacted needlessly without it.
"""

from collections import Counter
from dataclasses import dataclass, field

from ...enums import RootCause
from ...models import AtRiskRecord, Diagnosis
from .cohort import CohortSignal
from .deterministic import diagnose as diagnose_deterministic


@dataclass
class LayerScore:
    source: str
    total: int = 0
    correct: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


@dataclass
class AccuracyReport:
    layers: dict[str, LayerScore] = field(default_factory=dict)
    confusions: Counter = field(default_factory=Counter)
    total: int = 0
    correct: int = 0
    cohort_rescued: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "correct": self.correct,
            "accuracy": round(self.accuracy, 4),
            "cohort_rescued": self.cohort_rescued,
            "layers": {
                name: {"records": s.total, "correct": s.correct,
                       "accuracy": round(s.accuracy, 4)}
                for name, s in self.layers.items()
            },
            "top_confusions": [
                {"truth": t, "predicted": p, "count": n}
                for (t, p), n in self.confusions.most_common(5)
            ],
        }


def score(
    records: list[AtRiskRecord],
    diagnoses: dict[str, Diagnosis],
    truth: dict[str, RootCause],
) -> AccuracyReport:
    report = AccuracyReport()
    for r in records:
        d, actual = diagnoses[r.id], truth[r.id]
        layer = report.layers.setdefault(d.source, LayerScore(d.source))
        layer.total += 1
        report.total += 1
        if d.root_cause is actual:
            layer.correct += 1
            report.correct += 1
            if d.source == "cohort":
                report.cohort_rescued += 1
        else:
            report.confusions[(actual.value, d.root_cause.value)] += 1
    return report


def cohort_counterfactual(
    records: list[AtRiskRecord],
    signals: dict[str, CohortSignal],
    truth: dict[str, RootCause],
) -> dict:
    """What the batch would have done with the cohort signal switched off.

    A record the cohort identifies as an outage carries a generic decline. Absent
    the signal it reads as a customer-side failure and earns a message — a message
    to someone whose account was never short.
    """
    would_contact = [
        r for r in records
        if signals.get(r.id) and signals[r.id].indicates_outage
        and diagnose_deterministic(r) is None
    ]
    truly_downtime = [r for r in would_contact
                      if truth[r.id] is RootCause.BANK_DOWNTIME]
    return {
        "records_flagged_as_outage": len(would_contact),
        "correctly_identified": len(truly_downtime),
        "needless_contacts_prevented": len(truly_downtime),
        "issuer": next((signals[r.id].issuer for r in would_contact), None),
    }
