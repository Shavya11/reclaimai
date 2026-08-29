"""Seeded synthetic leak generator.

Seeded on purpose. Every number quoted in PROJECT.md must reproduce exactly on a
reviewer's machine — a batch that prints a different total than the README claims
reads as fabricated, however good the code is.
"""

import random
from dataclasses import dataclass, field
from datetime import timedelta

from ..enums import LeakType, RecordState, RootCause
from ..models import AtRiskRecord
from ..timeutil import IST, now
from . import error_codes as ec


def batch_epoch(at=None):
    """The anchor every timestamp in a batch is measured from: midnight IST of
    the day the batch is generated.

    Reading the wall clock directly would make amounts reproducible from the
    seed while timestamps quietly were not — and timestamps decide which records
    fall inside quiet hours and which hour bucket the cohort signal groups on.
    The batch would then produce a slightly different compliance count every run,
    which is exactly the kind of number a reader is entitled to reproduce.

    Anchoring on the day keeps records genuinely recent and ageing naturally,
    while holding every derived number still for as long as anyone is likely to
    be looking at it.
    """
    at = at or now()
    return at.replace(hour=0, minute=0, second=0, microsecond=0)

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

# ...which only works if those signals are actually present. They were not:
# a customer was drawn at random for every record regardless of cause, so a
# third of the INSUFFICIENT_FUNDS records landed on people with no payment
# history at all, at midday. Nothing in the context distinguished them, the
# model correctly answered UNKNOWN every time, and layer 2 scored 0% on the
# records it exists to resolve.
#
# Worse, the data contradicted the policy acting on it: policies.yaml retries
# these at next_salary_window and outcomes.py grants that a 41% success rate,
# which only makes sense for someone who normally pays and is short until
# payday. So the two signals below are not tuning the fixture to please the
# model - they are what the rest of the system already assumes is true.
#
# RISK_DECLINE is deliberately left with no tell. A fraud decline that
# announces itself is not a fraud decline, and UNKNOWN -> human is the correct
# handling for one.
NIGHT_HOURS = (21, 22, 23)

N_CUSTOMERS = 55
OPT_OUT_RATE = 0.08
DND_RATE = 0.15
# Raised from 0.20 so the pool of customers with a payment history is large
# enough to carry the INSUFFICIENT_FUNDS records without the same few people
# appearing over and over — which would distort the frequency-cap guardrail.
# Also the more honest figure: most customers whose payment fails at an
# established merchant have paid it before.
PRIOR_SUCCESS_RATE = 0.45

# V2 receivables. Deliberately a SEPARATE stream (see generate): appending to
# the payments RNG would shift every draw above it and silently invalidate every
# number V1 published.
N_INVOICES = 60
INVOICE_MIX: dict[RootCause, int] = {
    RootCause.PAYMENT_STALLED: 20,
    RootCause.AWAITING_APPROVAL: 13,
    RootCause.BUYER_CASH_CRUNCH: 11,
    RootCause.INVOICE_NOT_RECEIVED: 10,
    RootCause.INVOICE_DISPUTED: 6,
}
N_BUYERS = 22

# B2B tickets are an order of magnitude above consumer ones, which is the point:
# it makes the value ceiling fire hard and gives DSO something to move.
_INVOICE_TICKETS = [25_000, 48_000, 75_000, 120_000, 185_000, 320_000,
                    560_000, 890_000, 1_200_000]
_INVOICE_WEIGHTS = [16, 15, 14, 12, 10, 8, 6, 4, 2]

BUYER_ORGS = [
    "Sundaram Textiles", "Kaveri Logistics", "Nandi Steelworks",
    "Meridian Foods", "Anand Auto Components", "Prakash Chemicals",
    "Vertex Interiors", "Coastal Marine Supply", "Deccan Packaging",
    "Orion Electricals", "Bharat Agro Traders", "Silverline Pharma",
    "Trident Engineering", "Gokul Dairy Products", "Ashwin Printing",
    "Ravi Constructions", "Lotus Hospitality", "Zenith Instruments",
    "Kamal Garments", "Surya Solar Systems", "Indus Paper Mills",
    "Neelkanth Ceramics",
]

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
    # The records deliberately clustered into the outage hour. Stated rather
    # than left to be re-derived from (issuer, cause): a BANK_DOWNTIME record
    # outside the cluster can draw the outage issuer by chance, and a test that
    # guesses breaks the moment the RNG shifts under it.
    outage_ids: frozenset[str] = frozenset()
    # The buyer reply text, per record, for the records that answered at all.
    # Held on the batch rather than on the record because a reply is something
    # that arrives later — settlement delivers it, the record does not own it.
    replies: dict[str, str] = field(default_factory=dict)

    @property
    def total_at_risk(self) -> int:
        return sum(r.amount for r in self.records)


def _make_customers(rng: random.Random, epoch) -> list[Customer]:
    out = []
    for i in range(N_CUSTOMERS):
        cid = f"CUST_{4000 + i}"
        has_history = rng.random() < PRIOR_SUCCESS_RATE
        out.append(
            Customer(
                id=cid,
                email=f"customer{i}@example.com",
                phone=f"+9198{rng.randint(10000000, 99999999)}",
                # Assigned after records exist — see _assign_contact_flags.
                opted_out=False,
                on_dnd=False,
                successful_payments_lifetime=rng.randint(1, 9) if has_history else 0,
                last_successful_at=(
                    epoch - timedelta(days=rng.randint(20, 200)) if has_history else None
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


def _make_buyers(rng: random.Random, epoch) -> list[Customer]:
    """B2B counterparties. Same Customer shape as a consumer — AtRiskRecord and
    the guardrails stay generic, which is the whole reason a new leak type is a
    detector and a policy block rather than a schema migration.

    `successful_payments_lifetime` carries prior invoices paid, so the frequency
    cap, consent and DND guardrails treat an AP contact exactly as they treat a
    consumer. A finance team can opt out of dunning email too.
    """
    out = []
    for i, org in enumerate(BUYER_ORGS[:N_BUYERS]):
        slug = org.split()[0].lower()
        paid = rng.randint(0, 40)
        out.append(Customer(
            id=f"BUYER_{9000 + i}",
            email=f"accounts@{slug}.example.com",
            phone=f"+9180{rng.randint(10000000, 99999999)}",
            opted_out=False,
            on_dnd=False,
            successful_payments_lifetime=paid,
            last_successful_at=(epoch - timedelta(days=rng.randint(10, 120))
                                if paid else None),
        ))
    return out


def _invoice_amount(rng: random.Random) -> int:
    base = rng.choices(_INVOICE_TICKETS, weights=_INVOICE_WEIGHTS, k=1)[0]
    return int(round(base * rng.uniform(0.85, 1.3))) * 100


# How overdue an invoice is, given its cause. These are not decoration: the
# receivables prompt asks the model to judge lateness against the buyer's own
# average, so the gap between days_overdue and avg_days_to_pay has to carry the
# signal. An AWAITING_APPROVAL invoice is barely late for a slow payer; a
# PAYMENT_STALLED one is late by anyone's standard.
_OVERDUE_RANGE: dict[RootCause, tuple[int, int]] = {
    RootCause.INVOICE_NOT_RECEIVED: (5, 20),
    RootCause.AWAITING_APPROVAL: (3, 18),
    RootCause.BUYER_CASH_CRUNCH: (25, 70),
    RootCause.PAYMENT_STALLED: (20, 65),
    RootCause.INVOICE_DISPUTED: (15, 55),
}

_PAY_TERMS = (15, 30, 45, 60)


def _make_invoices(rng: random.Random, epoch, n: int, buyers: list[Customer]):
    """~n overdue invoices, drawn from their OWN rng.

    Every signal here is one the receivables prompt names. Nothing is present
    that the model is not asked to weigh, and nothing it is asked to weigh is
    absent — V1's hardest-won lesson was that a fixture which labels a record
    INSUFFICIENT_FUNDS while giving it no history makes UNKNOWN the only honest
    answer, and scores the model 0% for being right.
    """
    causes: list[RootCause] = []
    for cause, count in INVOICE_MIX.items():
        causes.extend([cause] * count)
    causes = causes[:n]
    while len(causes) < n:
        causes.append(RootCause.PAYMENT_STALLED)
    rng.shuffle(causes)

    records: list[AtRiskRecord] = []
    truth: dict[str, RootCause] = {}

    for i, cause in enumerate(causes):
        rid = f"INV_{7000 + i}"
        buyer = buyers[rng.randrange(len(buyers))]
        amount = _invoice_amount(rng)
        terms = rng.choice(_PAY_TERMS)

        lo, hi = _OVERDUE_RANGE[cause]
        days_overdue = rng.randint(lo, hi)
        due = epoch - timedelta(days=days_overdue)
        issued = due - timedelta(days=terms)

        # A buyer's own habit is the yardstick the prompt judges against.
        if cause is RootCause.AWAITING_APPROVAL:
            avg_days = terms + rng.randint(days_overdue + 2, days_overdue + 25)
        elif cause is RootCause.BUYER_CASH_CRUNCH:
            avg_days = terms + rng.randint(0, 6)
        else:
            avg_days = terms + rng.randint(0, 12)

        # Signals that distinguish the five causes. Each cause gets exactly the
        # tells the prompt says to read, and no others.
        partial = 0
        dispute = False
        reminders = rng.randint(1, 3)
        po_present = True

        if cause is RootCause.INVOICE_NOT_RECEIVED:
            reminders = 0
            po_present = rng.random() < 0.4
        elif cause is RootCause.INVOICE_DISPUTED:
            dispute = True
            po_present = rng.random() < 0.5
        elif cause is RootCause.BUYER_CASH_CRUNCH:
            # Paying part of a bill is the strongest single tell there is: it is
            # neither a dispute nor a lost document.
            partial = int(amount * rng.uniform(0.15, 0.45)) // 100 * 100
            reminders = rng.randint(2, 4)
        elif cause is RootCause.AWAITING_APPROVAL:
            reminders = rng.randint(0, 2)

        signals: dict = {
            "invoice_id": f"inv_{rng.getrandbits(40):010x}",
            "issued_at": issued.isoformat(),
            "days_overdue": days_overdue,
            "payment_terms_days": terms,
            "buyer_org": BUYER_ORGS[int(buyer.id.removeprefix("BUYER_")) - 9000],
            "ap_contact": buyer.email,
            "prior_invoices_paid": buyer.successful_payments_lifetime,
            "avg_days_to_pay": avg_days,
            "partial_paid_paise": partial,
            "reminders_sent": reminders,
            "dispute_flag": dispute,
            "po_number_present": po_present,
            "buyer_reply": None,   # arrives later, through settlement
        }

        records.append(AtRiskRecord(
            id=rid,
            leak_type=LeakType.OVERDUE_INVOICE,
            amount=amount,
            counterparty_id=buyer.id,
            source_ref=signals["invoice_id"],
            detected_at=due,      # the clock starts when it fell due
            due_at=due,
            raw_signals=signals,
            state=RecordState.AT_RISK,
        ))
        truth[rid] = cause

    return records, truth


def generate(seed: int = 42, n: int = 120, at=None,
             leak_types: "set[LeakType] | None" = None,
             n_invoices: int = N_INVOICES) -> Batch:
    epoch = batch_epoch(at)
    rng = random.Random(seed)
    customers = _make_customers(rng, epoch)
    by_id = {c.id: c for c in customers}

    causes: list[RootCause] = []
    for cause, count in MIX.items():
        causes.extend([cause] * count)
    causes = causes[:n]
    while len(causes) < n:
        causes.append(RootCause.INSUFFICIENT_FUNDS)
    rng.shuffle(causes)

    high_value_idx = set(rng.sample(range(n), HIGH_VALUE_COUNT))

    # Drawn from only when the cause is INSUFFICIENT_FUNDS; see NIGHT_HOURS.
    history_pool = [c.id for c in customers if c.successful_payments_lifetime > 0]

    # Bank-downtime records cluster on one issuer inside one hour. They carry the
    # generic "declined" error, so ONLY the cohort signal can identify them.
    outage_start = epoch - timedelta(hours=6)
    downtime_idx = [i for i, c in enumerate(causes) if c is RootCause.BANK_DOWNTIME]
    clustered = set(downtime_idx[:OUTAGE_COUNT])

    records: list[AtRiskRecord] = []
    truth: dict[str, RootCause] = {}
    outage_ids: set[str] = set()

    for i, cause in enumerate(causes):
        rid = f"REC_{5000 + i}"
        if cause is RootCause.INSUFFICIENT_FUNDS and history_pool:
            cust = by_id[history_pool[rng.randrange(len(history_pool))]]
        else:
            cust = by_id[f"CUST_{4000 + rng.randrange(N_CUSTOMERS)}"]
        leak = LEAK_TYPE_FOR.get(cause, LeakType.FAILED_PAYMENT)
        amount = _amount(rng, i in high_value_idx)

        if i in clustered:
            outage_ids.add(rid)
            issuer = OUTAGE_ISSUER
            detected = outage_start + timedelta(minutes=rng.randint(0, 59))
            error = dict(rng.choice(ec.AMBIGUOUS))  # outage disguised as a decline
        else:
            issuer = rng.choice(ec.ISSUERS)
            if cause is RootCause.INSUFFICIENT_FUNDS:
                night = epoch - timedelta(days=rng.randint(1, 3))
                detected = night.replace(hour=rng.choice(NIGHT_HOURS),
                                         minute=rng.randint(0, 59))
            else:
                detected = epoch - timedelta(hours=rng.randint(1, 72),
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

    _assign_contact_flags(customers, records, truth, rng)

    # --- V2: receivables, on their own stream ------------------------------
    #
    # Everything above this line is byte-for-byte what V1 produced, and it has
    # to stay that way: PROJECT.md, the README and `cli verify` all quote
    # numbers derived from it. Drawing invoices from `rng` would advance the
    # shared stream and change all 120 payment records — a change that is
    # invisible in a diff and fatal to every published figure. A second stream
    # seeded off the first keeps the batch reproducible AND additive.
    if n_invoices:
        inv_rng = random.Random(seed + 1)
        buyers = _make_buyers(inv_rng, epoch)
        inv_records, inv_truth = _make_invoices(inv_rng, epoch, n_invoices, buyers)
        _assign_buyer_flags(buyers, inv_records, inv_rng)
        records.extend(inv_records)
        customers.extend(buyers)
        truth.update(inv_truth)

    # Filtering happens last, after every draw is made, so that asking for a
    # subset returns exactly the records the full batch would have contained.
    # Filtering earlier would make the RNG depend on the filter and give
    # `--leak-types` a different batch rather than a smaller one.
    if leak_types is not None:
        keep = {r.id for r in records if r.leak_type in leak_types}
        records = [r for r in records if r.id in keep]
        truth = {k: v for k, v in truth.items() if k in keep}
        outage_ids = {i for i in outage_ids if i in keep}
        owners = {r.counterparty_id for r in records}
        customers = [c for c in customers if c.id in owners]

    return Batch(records=records, customers=customers, truth=truth,
                 traffic=_traffic(records, truth),
                 outage_ids=frozenset(outage_ids))


def _assign_buyer_flags(buyers, records, rng) -> None:
    """A finance team can opt out of dunning email and can sit on a DND list
    exactly as a consumer can, and the guardrails must be able to prove it. Two
    of each, guaranteed rather than hoped for — the same reasoning as
    _assign_contact_flags."""
    owners = sorted({r.counterparty_id for r in records})
    rng.shuffle(owners)
    by_id = {b.id: b for b in buyers}
    for bid in owners[:2]:
        by_id[bid].opted_out = True
    for bid in owners[2:5]:
        by_id[bid].on_dnd = True


# Causes whose policy row contacts the customer. Only these can ever trip the
# consent, DND, quiet-hours, cooldown or frequency-cap guardrails.
CONTACTING_CAUSES = frozenset({
    RootCause.INSUFFICIENT_FUNDS, RootCause.EXPIRED_INSTRUMENT,
    RootCause.INVALID_INSTRUMENT, RootCause.AUTH_DROPOFF,
    RootCause.CART_ABANDONMENT,
})


def _assign_contact_flags(customers, records, truth, rng) -> None:
    """Put opt-out and DND flags on customers who will actually be contacted.

    Assigning them at random leaves it to chance whether the consent guardrail
    ever fires — with 3 opted-out customers in 55, it usually does not. A
    fixture that cannot exercise a guardrail cannot demonstrate it, so coverage
    is guaranteed here rather than hoped for.
    """
    owners: dict[str, list[str]] = {}
    for r in records:
        if truth[r.id] in CONTACTING_CAUSES:
            owners.setdefault(r.counterparty_id, []).append(r.id)

    contactable = sorted(owners)
    rng.shuffle(contactable)
    by_id = {c.id: c for c in customers}

    n_opted = max(3, round(len(customers) * OPT_OUT_RATE))
    n_dnd = max(4, round(len(customers) * DND_RATE))

    for cid in contactable[:n_opted]:
        by_id[cid].opted_out = True
    for cid in contactable[n_opted:n_opted + n_dnd]:
        by_id[cid].on_dnd = True


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
        # An invoice has no issuer. Bucketing it under a placeholder would put
        # sixty records into one fake cohort and could manufacture an outage
        # signal out of accounts-receivable data.
        if not r.raw_signals.get("issuer_bank"):
            continue
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
