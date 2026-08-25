"""Seeded synthetic leak generator.

Seeded on purpose. Every number quoted in PROJECT.md must reproduce exactly on a
reviewer's machine — a batch that prints a different total than the README claims
reads as fabricated, however good the code is.
"""

import random
from dataclasses import dataclass
from datetime import timedelta

from ..enums import LeakType, RecordState, RootCause
from ..models import AtRiskRecord
from ..timeutil import IST, now
from . import error_codes as ec

# Sums to 120. Follows PROJECT.md §10, with small RISK_DECLINE and
# MANDATE_REVOKED slices carved out so the five no_auto_action policy rows
# actually have records to demonstrate.
MIX: dict[RootCause, int] = {
    RootCause.INSUFFICIENT_FUNDS: 33,
    RootCause.BANK_DOWNTIME: 24,
    RootCause.AUTH_DROPOFF: 18,
    RootCause.CART_ABANDONMENT: 18,
    RootCause.EXPIRED_INSTRUMENT: 17,
    RootCause.POLICY_BLOCK: 4,
    RootCause.RISK_DECLINE: 3,
    RootCause.MANDATE_REVOKED: 3,
}

LEAK_TYPE_FOR: dict[RootCause, LeakType] = {
    RootCause.CART_ABANDONMENT: LeakType.ABANDONED_CART,
    RootCause.MANDATE_REVOKED: LeakType.FAILED_MANDATE,
}

# These causes are indistinguishable from the error alone; the diagnosis has to
# come from history, amount, timing or the cohort signal.
AMBIGUOUS_CAUSES = frozenset({RootCause.INSUFFICIENT_FUNDS, RootCause.RISK_DECLINE})

N_CUSTOMERS = 55
OPT_OUT_RATE = 0.08
DND_RATE = 0.15
PRIOR_SUCCESS_RATE = 0.20

OUTAGE_ISSUER = "HDFC"
OUTAGE_COUNT = 15  # clustered inside one hour -> makes the cohort signal real
HIGH_VALUE_COUNT = 7  # above the ₹50,000 ceiling, so guardrail #8 fires


@dataclass
class Customer:
    id: str
    email: str
    phone: str
    opted_out: bool
    on_dnd: bool
    successful_payments_lifetime: int
    last_successful_at: object | None


# A failure count alone cannot say "outage" — ten failures out of twelve attempts
# and ten out of a thousand are different worlds. The simulation therefore carries
# the attempt volume the merchant saw, so the cohort signal divides by something
# real instead of an invented denominator.
BASELINE_FAILURE_RATE = 0.04
OUTAGE_FAILURE_RATE = 0.71


@dataclass
class Batch:
    records: list[AtRiskRecord]
    customers: list[Customer]
    truth: dict[str, RootCause]  # record_id -> the cause we planted
    traffic: dict[str, int]      # "ISSUER|YYYY-MM-DDTHH" -> total attempts

    @property
    def total_at_risk(self) -> int:
        return sum(r.amount for r in self.records)


def _make_customers(rng: random.Random) -> list[Customer]:
    out = []
    for i in range(N_CUSTOMERS):
        cid = f"CUST_{4000 + i}"
        has_history = rng.random() < PRIOR_SUCCESS_RATE
        out.append(
            Customer(
                id=cid,
                email=f"customer{i}@example.com",
                phone=f"+9198{rng.randint(10000000, 99999999)}",
                opted_out=rng.random() < OPT_OUT_RATE,
                on_dnd=rng.random() < DND_RATE,
                successful_payments_lifetime=rng.randint(1, 9) if has_history else 0,
                last_successful_at=(
                    now() - timedelta(days=rng.randint(20, 200)) if has_history else None
                ),
            )
        )
    return out


# Real merchant traffic is a long tail: most tickets are small, a few are large.
# A uniform spread over ₹199-₹85,000 would put the average an order of magnitude
# too high and make the value-ceiling guardrail look like it fires constantly.
_TICKETS = [199, 349, 499, 699, 899, 1299, 1999, 2999, 4999, 7999, 12999, 24999]
_TICKET_WEIGHTS = [14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 3, 2]


def _amount(rng: random.Random, high_value: bool) -> int:
    """₹199-₹85,000 in paise."""
    if high_value:
        return rng.randint(50_001, 85_000) * 100
    base = rng.choices(_TICKETS, weights=_TICKET_WEIGHTS, k=1)[0]
    return max(199, int(round(base * rng.uniform(0.9, 1.25)))) * 100


def generate(seed: int = 42, n: int = 120) -> Batch:
    rng = random.Random(seed)
    customers = _make_customers(rng)
    by_id = {c.id: c for c in customers}

    causes: list[RootCause] = []
    for cause, count in MIX.items():
        causes.extend([cause] * count)
    causes = causes[:n]
    while len(causes) < n:
        causes.append(RootCause.INSUFFICIENT_FUNDS)
    rng.shuffle(causes)

    high_value_idx = set(rng.sample(range(n), HIGH_VALUE_COUNT))

    # Bank-downtime records cluster on one issuer inside one hour. They carry the
    # generic "declined" error, so ONLY the cohort signal can identify them.
    outage_start = now() - timedelta(hours=6)
    downtime_idx = [i for i, c in enumerate(causes) if c is RootCause.BANK_DOWNTIME]
    clustered = set(downtime_idx[:OUTAGE_COUNT])

    records: list[AtRiskRecord] = []
    truth: dict[str, RootCause] = {}

    for i, cause in enumerate(causes):
        rid = f"REC_{5000 + i}"
        cust = by_id[f"CUST_{4000 + rng.randrange(N_CUSTOMERS)}"]
        leak = LEAK_TYPE_FOR.get(cause, LeakType.FAILED_PAYMENT)
        amount = _amount(rng, i in high_value_idx)

        if i in clustered:
            issuer = OUTAGE_ISSUER
            detected = outage_start + timedelta(minutes=rng.randint(0, 59))
            error = dict(rng.choice(ec.AMBIGUOUS))  # outage disguised as a decline
        else:
            issuer = rng.choice(ec.ISSUERS)
            detected = now() - timedelta(hours=rng.randint(1, 72),
                                         minutes=rng.randint(0, 59))
            if leak is LeakType.ABANDONED_CART:
                error = None  # no payment was ever attempted
            elif cause in AMBIGUOUS_CAUSES:
                error = dict(rng.choice(ec.AMBIGUOUS))
            else:
                error = dict(ec.SPECIFIC[cause.value])

        signals: dict = {
            "issuer_bank": issuer,
            "method": rng.choice(ec.METHODS) if leak is LeakType.FAILED_PAYMENT else "card",
            "attempt_number": 1,
            "customer_history": {
                "successful_payments_lifetime": cust.successful_payments_lifetime,
                "failed_payments_last_30d": rng.randint(0, 3),
                "same_instrument_succeeded_before": cust.successful_payments_lifetime > 0,
                "last_successful_at": (
                    cust.last_successful_at.isoformat() if cust.last_successful_at else None
                ),
            },
        }
        if leak is LeakType.ABANDONED_CART:
            signals["error"] = None  # never attempted payment at all
            signals["cart_age_minutes"] = rng.randint(30, 2880)
        else:
            signals["error"] = error
            signals["card_network"] = rng.choice(ec.NETWORKS)
            signals["card_type"] = rng.choice(["debit", "credit"])

        records.append(
            AtRiskRecord(
                id=rid,
                leak_type=leak,
                amount=amount,
                counterparty_id=cust.id,
                source_ref=f"pay_{rng.getrandbits(48):012x}",
                detected_at=detected,
                raw_signals=signals,
                state=RecordState.AT_RISK,
            )
        )
        truth[rid] = cause

    return Batch(records=records, customers=customers, truth=truth,
                 traffic=_traffic(records, truth))


def bucket_key(issuer: str, dt) -> str:
    """(issuer, hour) is the grain the cohort signal groups on."""
    return f"{issuer}|{dt.astimezone(IST).strftime('%Y-%m-%dT%H')}"


def _traffic(records, truth) -> dict[str, int]:
    """Back out plausible attempt volumes from the failures we planted, so that
    failures/attempts lands near the baseline normally and near the outage rate
    inside the outage window."""
    failures: dict[str, int] = {}
    outage_buckets: set[str] = set()
    for r in records:
        key = bucket_key(r.raw_signals["issuer_bank"], r.detected_at)
        failures[key] = failures.get(key, 0) + 1
        if (truth[r.id] is RootCause.BANK_DOWNTIME
                and r.raw_signals["issuer_bank"] == OUTAGE_ISSUER):
            outage_buckets.add(key)

    traffic: dict[str, int] = {}
    for key, n in failures.items():
        rate = OUTAGE_FAILURE_RATE if key in outage_buckets else BASELINE_FAILURE_RATE
        traffic[key] = max(n, round(n / rate))
    return traffic
