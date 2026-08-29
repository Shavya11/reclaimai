"""The human queue: what a person is actually asked to do.

Both jobs in this module exist because a person's attention is the scarcest
resource in the system, and a queue that wastes it is worse than no queue.
PLAN.md learned this once already, when a value ceiling meant for consumer cards
was pointed at B2B invoices and sent 48 of 60 to a human — "not restraint, a
queue nobody can work."

**Closing what no longer needs anyone.** `HumanQueueRow.resolved_at` has existed
since Day 1 and nothing ever wrote to it, so a record escalated on Monday and
paid on Friday sat in the queue for ever. Guardrail 11 stops the AGENT chasing
money that already arrived; until now nothing stopped a PERSON being sent to do
the same thing.

**Ordering what does.** The queue sorted by amount descending and nothing else,
so a revoked mandate worth ₹80,000 — where the correct action is none, and the
policy already said so — outranked a ₹40,000 payment that was recoverable and
waiting on a signature.

The ranking never chooses an action. It orders a list; a human still decides
every row. That is the same boundary the model sits behind everywhere else.
"""

from datetime import datetime
from enum import IntEnum
from typing import Any

from .clock import now
from .db import AtRiskRecordRow, HumanQueueRow, SessionLocal
from .enums import CAUSES_FOR_LEAK, LeakType, RootCause
from .money import format_inr
from .timeutil import to_ist

# Reused rather than restated. These priors are the single source of the
# system's belief about what recovers, and copying them into a second file is
# how two numbers that must agree start to drift. They are *stated estimates*,
# not measured rates — the same disclosure the scoreboard already carries.
from .synthetic.outcomes import ATTEMPT_DECAY, BASE_SUCCESS


class Tier(IntEnum):
    """What kind of attention a row needs. A hard partition, never blended with
    the score: no amount of money in tier 2 should outrank a signature that the
    agent is sitting idle waiting for."""

    BLOCKING = 1
    JUDGEMENT = 2
    FOR_THE_RECORD = 3


TIER_LABEL: dict[Tier, str] = {
    Tier.BLOCKING: "Blocking — agent is waiting on you",
    Tier.JUDGEMENT: "Needs judgement",
    Tier.FOR_THE_RECORD: "For the record — no action required",
}

# Causes whose policy row is `no_auto_action` because chasing them is WRONG, not
# because the agent is unsure. There is nothing for a person to do here either:
# the decision was already made correctly and this is its receipt.
#
# INVOICE_DISPUTED is deliberately absent. A dispute is also never chased, but it
# is a conversation somebody has to have about what is owed — that is real work,
# so it belongs in JUDGEMENT.
STOP_CAUSES = frozenset({
    RootCause.RISK_DECLINE,
    RootCause.MANDATE_REVOKED,
    RootCause.POLICY_BLOCK,
})

# How fast the recoverable value decays, in days. Intent dies at very different
# speeds: somebody who abandoned a cart this morning has probably bought it
# elsewhere by tomorrow, while a B2B invoice moves at the speed of an approval
# cycle and is worth roughly the same in three weeks as it is today.
HALF_LIFE_DAYS: dict[LeakType, float] = {
    LeakType.ABANDONED_CART: 0.5,
    LeakType.FAILED_PAYMENT: 5.0,
    LeakType.FAILED_MANDATE: 14.0,
    LeakType.OVERDUE_INVOICE: 45.0,
}

# A leak type added later decays at the fastest rate rather than the slowest, so
# a missing entry under-ranks a row instead of parking it at the top of a
# person's list for ever.
DEFAULT_HALF_LIFE = 0.5


# --- closing --------------------------------------------------------------


def resolve(record_id: str, *, session=None) -> int:
    """Close every open queue row for a record. Returns how many closed.

    Called when a record reaches a terminal state — recovered, or closed by a
    stopping rule. Either way the money is no longer anybody's to chase, and the
    row is a person's time being spent on a decision that already happened.

    `resolved_at` is stamped; `reason` is left alone. The reason a row exists is
    why it was ESCALATED, and overwriting it with why it closed would destroy
    the more useful of the two. Why it closed is derivable from the record's
    final state, which is where it is read from.

    Accepts a caller's session because both call sites are already inside one,
    and opening a second write transaction against the same SQLite file from
    inside the first is how a deadlock gets introduced by accident. When given a
    session this does not commit — the caller's commit carries it.
    """
    if session is not None:
        return _resolve_in(session, record_id)
    with SessionLocal() as owned:
        closed = _resolve_in(owned, record_id)
        owned.commit()
        return closed


def _resolve_in(session, record_id: str) -> int:
    rows = (session.query(HumanQueueRow)
            .filter(HumanQueueRow.record_id == record_id)
            .filter(HumanQueueRow.resolved_at.is_(None))
            .all())
    stamp = now()
    for row in rows:
        row.resolved_at = stamp
    return len(rows)


# --- ranking --------------------------------------------------------------


def tier_for(root_cause: str | None, *, leak_type: str, amount: int) -> Tier:
    """Which lane a row belongs in.

    Stop causes are checked before the ceiling, because a revoked mandate for
    ₹2 lakh is not a large decision awaiting a signature — it is not a decision
    at all.
    """
    if root_cause in {c.value for c in STOP_CAUSES}:
        return Tier.FOR_THE_RECORD

    from .brain.guardrails.rules.value_ceiling import ceiling_for

    if amount > ceiling_for(leak_type):
        return Tier.BLOCKING
    return Tier.JUDGEMENT


def prior_for(root_cause: str | None, leak_type: str) -> tuple[float, bool]:
    """P(recover), and whether it is an estimate rather than a prior.

    An `UNKNOWN` record has no cause, so it has no prior — that is the honest
    position and it is exactly why the row is in front of a person. Rather than
    print a confident number we do not have, it is scored on the mean of the
    causes that leak type could turn out to have, and the row says so.
    """
    known = {c.value: c for c in RootCause}
    cause = known.get(root_cause or "")
    if cause is not None and cause is not RootCause.UNKNOWN:
        return BASE_SUCCESS.get(cause, 0.0), False

    leak = {t.value: t for t in LeakType}.get(leak_type)
    candidates = CAUSES_FOR_LEAK.get(leak, frozenset()) if leak else frozenset()
    live = [BASE_SUCCESS[c] for c in candidates if BASE_SUCCESS.get(c, 0.0) > 0]
    if not live:
        return 0.0, True
    return sum(live) / len(live), True


def urgency(leak_type: str, age_days: float) -> float:
    """What fraction of the value is still reachable after waiting this long."""
    leak = {t.value: t for t in LeakType}.get(leak_type)
    half_life = HALF_LIFE_DAYS.get(leak, DEFAULT_HALF_LIFE) if leak else DEFAULT_HALF_LIFE
    return 0.5 ** (max(0.0, age_days) / half_life)


def expected_value(
    *,
    amount: int,
    root_cause: str | None,
    leak_type: str,
    attempts: int,
    age_days: float,
) -> tuple[int, bool]:
    """Money actually reachable, in paise, and whether the prior was estimated.

    Amount alone ranks a dead mandate above a live payment. This is the number
    that says a ₹80,000 record worth nothing should be below a ₹40,000 record
    worth ₹16,400.
    """
    prior, estimated = prior_for(root_cause, leak_type)
    decayed = prior * (ATTEMPT_DECAY ** max(0, attempts))
    return int(round(amount * decayed * urgency(leak_type, age_days))), estimated


def _age_days(raised_at: datetime | None, at: datetime) -> float:
    """SQLite hands back a naive datetime even for a `timezone=True` column, so
    every read has to be normalised before it can be subtracted from the clock.
    `to_ist` is the one place that decision lives."""
    if raised_at is None:
        return 0.0
    return max(0.0, (to_ist(at) - to_ist(raised_at)).total_seconds() / 86_400.0)


def open_items(causes: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Every unresolved row, enriched and ordered: tier first, then value.

    Resolved rows are not returned. They are not work, and a queue that shows
    finished work is the problem this module exists to fix.
    """
    if causes is None:
        from .scoreboard import diagnosed_causes

        causes = diagnosed_causes()

    at = now()
    items: list[dict[str, Any]] = []
    with SessionLocal() as session:
        rows = (session.query(HumanQueueRow)
                .filter(HumanQueueRow.resolved_at.is_(None))
                .all())
        records = {
            r.id: r for r in session.query(AtRiskRecordRow)
            .filter(AtRiskRecordRow.id.in_([r.record_id for r in rows])).all()
        } if rows else {}

        for row in rows:
            record = records.get(row.record_id)
            leak_type = record.leak_type if record else ""
            attempts = record.attempts or 0 if record else 0
            cause = causes.get(row.record_id)
            age = _age_days(row.raised_at, at)

            tier = tier_for(cause, leak_type=leak_type, amount=row.amount)
            ev, estimated = expected_value(
                amount=row.amount, root_cause=cause, leak_type=leak_type,
                attempts=attempts, age_days=age,
            )

            items.append({
                "id": row.id,
                "record_id": row.record_id,
                "reason": row.reason,
                "amount_paise": row.amount,
                "amount_display": format_inr(row.amount),
                "root_cause": cause,
                "raised_at": row.raised_at.isoformat() if row.raised_at else None,
                "resolved_at": None,
                "leak_type": leak_type,
                "tier": int(tier),
                "tier_label": TIER_LABEL[tier],
                "ev_paise": ev,
                "ev_display": format_inr(ev),
                "ev_is_estimate": estimated,
                "days_waiting": round(age, 1),
            })

    items.sort(key=lambda i: (i["tier"], -i["ev_paise"], -i["amount_paise"]))
    return items


def resolved_count() -> int:
    """Rows that closed themselves before a human reached them.

    Reported next to the open count rather than folded into it: "54 escalated"
    and "43 still need a person" are both true, and only one of them is the
    number to quote at a merchant deciding how to staff this.
    """
    with SessionLocal() as session:
        return (session.query(HumanQueueRow)
                .filter(HumanQueueRow.resolved_at.isnot(None))
                .count())
