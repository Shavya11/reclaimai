"""Replays customer responses as signed Razorpay webhooks.

Read the honesty note before the code. **The payloads are simulated. The chain
they travel is not.** Each one is signed with HMAC-SHA256, posted through the
same `receive()` a live Razorpay delivery hits, verified, deduplicated, and
walked back link -> intervention -> record before a single rupee is counted.
Nothing here writes to `interventions` or moves a record to RECOVERED directly.

That distinction is the point. `cloudflared` is not installed on the build
machine, so there is no public URL for Razorpay to reach; without this the
attribution code would ship untested and the scoreboard would have to be
computed by asking the outcome simulator what it thinks happened. Instead the
simulator only decides *whether the customer paid* — the same judgement call
PROJECT.md §10 already discloses as modelled — and everything downstream of
that decision is the production path.

Which probability applies comes from the record's TRUE root cause, not our
diagnosis. The world does not care what we concluded. Diagnosing an outage as
insufficient funds still gets the 0.75 retry, and diagnosing a dead mandate as
recoverable still gets nothing — which is exactly why a wrong diagnosis costs
contacts rather than being invisible in the numbers.
"""

import json
import logging
import random
from dataclasses import dataclass, field
from typing import Any

from .config import settings
from .db import AtRiskRecordRow, InterventionRow, SessionLocal, init_db
from .enums import ActionType, RootCause
from .synthetic import razorpay_payloads as payloads
from .synthetic.outcomes import probability
from .webhooks import receive, sign
from .webhooks.attribution import RESULT_RECOVERED, mark_no_response

log = logging.getLogger(__name__)

# Indian salary credits cluster on the 1st-3rd. An INSUFFICIENT_FUNDS retry
# landing inside that window is the single highest-leverage timing decision the
# policy table makes, and this is where it gets paid for.
SALARY_DAYS = (1, 2, 3)


@dataclass
class SettlementResult:
    pending: int = 0
    recovered: int = 0
    recovered_paise: int = 0
    no_response: int = 0
    failed_again: int = 0
    unattributed: int = 0
    duplicates: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pending": self.pending,
            "recovered": self.recovered,
            "recovered_paise": self.recovered_paise,
            "no_response": self.no_response,
            "failed_again": self.failed_again,
            "unattributed": self.unattributed,
            "duplicates": self.duplicates,
        }


def _pending() -> list[dict[str, Any]]:
    """Interventions that fired and have not yet heard back. Reading them out as
    plain dicts keeps the session short — attribution opens its own."""
    with SessionLocal() as session:
        rows = (session.query(InterventionRow, AtRiskRecordRow)
                .join(AtRiskRecordRow,
                      AtRiskRecordRow.id == InterventionRow.record_id)
                .filter(InterventionRow.outcome == "EXECUTED")
                .filter(InterventionRow.result.is_(None))
                .filter(InterventionRow.razorpay_ref.isnot(None))
                .order_by(InterventionRow.id)
                .all())
        return [{
            "id": i.id, "record_id": i.record_id, "action_type": i.action_type,
            "attempt_number": i.attempt_number, "razorpay_ref": i.razorpay_ref,
            "scheduled_for": i.scheduled_for, "amount": r.amount,
        } for i, r in rows]


def _deliver(body: dict, *, event_id: str, secret: str) -> Any:
    """Sign the exact bytes that will be verified. Serialising once and passing
    the bytes around — rather than the dict — is the whole discipline: the
    signature covers what the receiver reads, byte for byte."""
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    return receive(raw, sign(raw, secret), event_id=event_id, secret=secret,
                   simulated=True)


def settle(
    truth: dict[str, RootCause],
    *,
    seed: int = 42,
    secret: str | None = None,
) -> SettlementResult:
    """Decide each pending intervention's fate and deliver the corresponding
    webhook. Safe to run repeatedly: settled interventions are skipped, and a
    redelivered event is caught by UNIQUE(event_id) anyway."""
    init_db()
    secret = settings.webhook_secret if secret is None else secret
    result = SettlementResult()

    for item in _pending():
        result.pending += 1
        rid = item["record_id"]
        cause = truth.get(rid, RootCause.UNKNOWN)

        try:
            action = ActionType(item["action_type"])
        except ValueError:
            action = ActionType.NO_ACTION

        scheduled = item["scheduled_for"]
        in_salary_window = bool(scheduled and scheduled.day in SALARY_DAYS)

        # Keyed on (record, attempt) — deliberately NOT on the intervention id.
        # The naive baseline in baseline.py draws from the identical key, so
        # record REC_5041's second attempt gets the same coin flip under both
        # strategies. Any difference in the two scoreboards is then a
        # difference in strategy, not a difference in luck, which is the only
        # way the comparison means anything.
        rng = random.Random(f"{seed}:{rid}:{item['attempt_number']}")
        p = probability(cause, action=action, attempt_number=item["attempt_number"],
                        in_salary_window=in_salary_window)
        paid = rng.random() < p

        ref = item["razorpay_ref"]
        amount = item["amount"]
        payment_id = f"pay_{rng.getrandbits(48):012x}"

        if paid:
            if action is ActionType.SILENT_RETRY:
                body = payloads.order_paid(order_id=ref, payment_id=payment_id,
                                           amount=amount, record_id=rid)
                event = "order.paid"
            else:
                body = payloads.payment_link_paid(
                    link_id=ref, payment_id=payment_id, amount=amount,
                    record_id=rid, attempt=item["attempt_number"])
                event = "payment_link.paid"
        elif action is ActionType.SILENT_RETRY:
            # A retry that reached the bank and lost gets a real failure event.
            # A link the customer never opened produces no webhook at all —
            # silence is not an event, and pretending otherwise would invent
            # traffic Razorpay never sent.
            body = payloads.payment_failed(
                payment_id=payment_id, amount=amount, record_id=rid,
                order_id=ref, description="The bank could not process the retry.")
            event = "payment.failed"
        else:
            mark_no_response(item["id"],
                             reason=f"Link {ref} delivered; not paid "
                                    f"(modelled p={p:.2f} for {cause.value}).")
            result.no_response += 1
            continue

        reception = _deliver(body, event_id=f"evt_{item['id']}_{event}",
                             secret=secret)
        result.events.append(reception.as_dict())

        # Counted off the receiver's verdict, never off our own intention.
        # ALREADY_ATTRIBUTED means the money was someone else's recovery, and
        # tallying it here is how a scoreboard quietly starts double-counting.
        if reception.outcome == "DUPLICATE":
            result.duplicates += 1
        elif reception.outcome == "UNATTRIBUTED":
            result.unattributed += 1
        elif reception.outcome == "ALREADY_ATTRIBUTED":
            result.duplicates += 1
        elif reception.outcome == "PROCESSED" and reception.attribution is not None:
            if paid:
                result.recovered += 1
                result.recovered_paise += reception.attribution.amount
            else:
                result.failed_again += 1

    return result


def recovered_total() -> int:
    with SessionLocal() as session:
        return sum(
            amount for (amount,) in session.query(InterventionRow.recovered_amount)
            .filter(InterventionRow.result == RESULT_RECOVERED).all()
        )
