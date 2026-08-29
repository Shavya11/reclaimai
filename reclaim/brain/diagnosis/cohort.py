"""Layer 1.5: the cohort signal.

One record saying "declined by the bank" is a customer problem. Forty records
saying it, all on one issuer, all inside one hour, is an issuer problem — and the
correct response inverts completely: retry quietly in twenty minutes and tell the
customer nothing, rather than SMS-blasting people to top up an account that was
never short.

Computed over the whole batch BEFORE diagnosis, because a per-record view cannot
see it by construction.
"""

from dataclasses import dataclass

from ...models import AtRiskRecord
from ...timeutil import IST

# Above this multiple of the merchant's baseline, an issuer is having an outage
# rather than a bad afternoon. Deliberately conservative: a false BANK_DOWNTIME
# means we stay silent on a customer who genuinely needed telling.
OUTAGE_RATIO = 4.0
MIN_FAILURES_FOR_SIGNAL = 5


@dataclass(frozen=True)
class CohortSignal:
    issuer: str
    bucket: str
    failures: int
    attempts: int
    failure_rate: float
    baseline_rate: float

    @property
    def ratio(self) -> float:
        return self.failure_rate / self.baseline_rate if self.baseline_rate else 0.0

    @property
    def indicates_outage(self) -> bool:
        return (self.failures >= MIN_FAILURES_FOR_SIGNAL
                and self.ratio >= OUTAGE_RATIO)

    def as_dict(self) -> dict:
        return {
            "same_issuer_failure_rate_last_1h": round(self.failure_rate, 4),
            "baseline_failure_rate": round(self.baseline_rate, 4),
            "ratio_vs_baseline": round(self.ratio, 2),
            "failures_in_bucket": self.failures,
            "attempts_in_bucket": self.attempts,
            "indicates_outage": self.indicates_outage,
        }


def bucket_key(issuer: str, dt) -> str:
    return f"{issuer}|{dt.astimezone(IST).strftime('%Y-%m-%dT%H')}"


def compute(records: list[AtRiskRecord], traffic: dict[str, int]) -> dict[str, CohortSignal]:
    """record_id -> the cohort signal for that record's (issuer, hour) bucket.

    Only records that name an issuer take part. An overdue invoice has no
    issuer and no attempt behind it, so including one would both invent a
    cohort out of accounts-receivable data and drag the merchant's baseline
    failure rate — the denominator every outage judgement divides by — toward a
    number no bank was involved in producing.
    """
    scoped = [r for r in records if r.raw_signals.get("issuer_bank")]

    failures: dict[str, int] = {}
    for r in scoped:
        key = bucket_key(r.raw_signals["issuer_bank"], r.detected_at)
        failures[key] = failures.get(key, 0) + 1

    total_failures = sum(failures.values())
    total_attempts = sum(traffic.get(k, n) for k, n in failures.items())
    baseline = total_failures / total_attempts if total_attempts else 0.0

    out: dict[str, CohortSignal] = {}
    for r in scoped:
        issuer = r.raw_signals["issuer_bank"]
        key = bucket_key(issuer, r.detected_at)
        n = failures[key]
        attempts = traffic.get(key, n)
        out[r.id] = CohortSignal(
            issuer=issuer, bucket=key, failures=n, attempts=attempts,
            failure_rate=n / attempts if attempts else 0.0,
            baseline_rate=baseline,
        )
    return out


def outage_buckets(signals: dict[str, CohortSignal]) -> set[str]:
    return {s.bucket for s in signals.values() if s.indicates_outage}
