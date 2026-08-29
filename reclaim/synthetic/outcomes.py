"""Outcome simulator.

Real Razorpay APIs, real error codes, real payment links — customer *response*
is modelled. Say that out loud in the demo. Claiming 90% recovery invites
suspicion; 35% with an honest unrecoverable list reads as credible.

Every RootCause must appear here. `cli verify` enforces that, because a missing
entry silently skews the batch numbers.
"""

import random

from ..enums import ActionType, RootCause

# P(recovered) given the intervention actually fired.
BASE_SUCCESS: dict[RootCause, float] = {
    RootCause.BANK_DOWNTIME: 0.75,        # the bank comes back; the retry just works
    RootCause.TECHNICAL_ERROR: 0.60,
    RootCause.INSUFFICIENT_FUNDS: 0.12,   # immediate retry; salary timing lifts this
    RootCause.LIMIT_EXCEEDED: 0.35,
    RootCause.AUTH_DROPOFF: 0.29,         # UPI removes the OTP step
    RootCause.CART_ABANDONMENT: 0.22,
    RootCause.EXPIRED_INSTRUMENT: 0.18,
    RootCause.INVALID_INSTRUMENT: 0.15,
    # V2 receivables. Higher across the board than payments, and that is the
    # honest shape of it: a business that owes money usually intends to pay, so
    # the constraint is attention and process rather than a dead card. The
    # ordering is what matters — an invoice nobody received converts far better
    # than a buyer who is genuinely short.
    RootCause.INVOICE_NOT_RECEIVED: 0.55,
    RootCause.AWAITING_APPROVAL: 0.48,
    RootCause.PAYMENT_STALLED: 0.30,
    RootCause.BUYER_CASH_CRUNCH: 0.20,
    RootCause.INVOICE_DISPUTED: 0.0,      # escalated to a person, never chased
    RootCause.RISK_DECLINE: 0.0,          # retrying looks like card testing
    RootCause.MANDATE_REVOKED: 0.0,       # the mandate is gone
    RootCause.POLICY_BLOCK: 0.0,          # correctly escalated, never chased
    RootCause.UNKNOWN: 0.0,               # never auto-acted on
}

# Retrying INSUFFICIENT_FUNDS on the 1st is the single highest-leverage timing
# decision in the system: 12% -> 41%.
SALARY_WINDOW_SUCCESS = 0.41

# A promise to pay, once made, is kept about three times in five. That number is
# the entire reason the promise state machine earns its place: going quiet until
# the promised date and then acting is worth more than continuing to dun, but
# only because the other two in five exist and have to be caught.
PROMISE_KEPT = 0.62

# Each further attempt on the same record is worth less than the last.
ATTEMPT_DECAY = 0.6


def probability(
    cause: RootCause,
    *,
    action: ActionType,
    attempt_number: int = 1,
    in_salary_window: bool = False,
    on_promised_date: bool = False,
) -> float:
    if action is ActionType.NO_ACTION or action is ActionType.ESCALATE:
        return 0.0
    if on_promised_date:
        # Acting on the date somebody named is not the same event as the Nth
        # rung of a ladder, so it does not decay with the attempt count. The
        # promise reset the clock; that is what a promise is.
        return PROMISE_KEPT
    if cause is RootCause.INSUFFICIENT_FUNDS and in_salary_window:
        p = SALARY_WINDOW_SUCCESS
    else:
        p = BASE_SUCCESS[cause]
    return round(p * (ATTEMPT_DECAY ** (attempt_number - 1)), 4)


def simulate(cause: RootCause, *, rng: random.Random, **kw) -> bool:
    return rng.random() < probability(cause, **kw)
