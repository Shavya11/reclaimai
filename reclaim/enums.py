"""Closed enums. RootCause being closed is what makes LLM hallucination harmless:
the model can only ever return a member of a fixed set, and the policy table below
it is deterministic."""

from enum import Enum


class LeakType(str, Enum):
    FAILED_PAYMENT = "FAILED_PAYMENT"
    ABANDONED_CART = "ABANDONED_CART"
    FAILED_MANDATE = "FAILED_MANDATE"
    OVERDUE_INVOICE = "OVERDUE_INVOICE"  # V2 — detector not built in V1


class RecordState(str, Enum):
    AT_RISK = "AT_RISK"
    IN_PROGRESS = "IN_PROGRESS"
    PROMISED = "PROMISED"
    RECOVERED = "RECOVERED"
    UNRECOVERABLE = "UNRECOVERABLE"
    ESCALATED = "ESCALATED"
    CLOSED = "CLOSED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATES


_TERMINAL_STATES = frozenset(
    {RecordState.RECOVERED, RecordState.UNRECOVERABLE, RecordState.CLOSED}
)


class RootCause(str, Enum):
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    BANK_DOWNTIME = "BANK_DOWNTIME"
    EXPIRED_INSTRUMENT = "EXPIRED_INSTRUMENT"
    INVALID_INSTRUMENT = "INVALID_INSTRUMENT"
    AUTH_DROPOFF = "AUTH_DROPOFF"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    RISK_DECLINE = "RISK_DECLINE"
    POLICY_BLOCK = "POLICY_BLOCK"
    CART_ABANDONMENT = "CART_ABANDONMENT"
    MANDATE_REVOKED = "MANDATE_REVOKED"
    TECHNICAL_ERROR = "TECHNICAL_ERROR"
    # V2 — receivables. A B2B invoice does not fail, it goes unanswered, and the
    # reasons are organisational rather than technical: nobody received it,
    # somebody disputes it, somebody has not approved it, the buyer is short, or
    # it has simply stalled. None of them are visible in an error code, which is
    # why layer 2 does more work on this leak type than on payments.
    INVOICE_NOT_RECEIVED = "INVOICE_NOT_RECEIVED"
    INVOICE_DISPUTED = "INVOICE_DISPUTED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    BUYER_CASH_CRUNCH = "BUYER_CASH_CRUNCH"
    PAYMENT_STALLED = "PAYMENT_STALLED"
    UNKNOWN = "UNKNOWN"


# Retrying these looks like card testing to an issuer, or chases a dead mandate.
# Enforced at the policy layer; stated here so the fact travels with the enum.
NEVER_RETRY = frozenset(
    {RootCause.RISK_DECLINE, RootCause.MANDATE_REVOKED, RootCause.POLICY_BLOCK}
)


class ActionType(str, Enum):
    RETRY = "RETRY"
    SILENT_RETRY = "SILENT_RETRY"
    SEND_LINK = "SEND_LINK"
    NOTIFY = "NOTIFY"
    ESCALATE = "ESCALATE"
    NO_ACTION = "NO_ACTION"

    @property
    def contacts_customer(self) -> bool:
        """Drives quiet hours, DND and the frequency cap. Silent retries are exempt
        precisely because they reach the bank, not the person."""
        return self in _CONTACTING_ACTIONS


_CONTACTING_ACTIONS = frozenset(
    {ActionType.SEND_LINK, ActionType.NOTIFY}
)


# Which causes are even possible for which leak type.
#
# V1 could leave this implicit because every cause was a payment cause. It
# cannot now: EXPIRED_INSTRUMENT is not a thing that happens to an invoice, and
# INVOICE_DISPUTED is not a thing that happens to a card. The map is the single
# source for three separate jobs — the policy table's coverage test, `cli
# verify`, and the enum the model is actually offered, which is narrowed per
# leak type so the closed-set guarantee stays as tight as the domain allows.
_RECEIVABLES_CAUSES = frozenset({
    RootCause.INVOICE_NOT_RECEIVED,
    RootCause.INVOICE_DISPUTED,
    RootCause.AWAITING_APPROVAL,
    RootCause.BUYER_CASH_CRUNCH,
    RootCause.PAYMENT_STALLED,
})

CAUSES_FOR_LEAK: dict[LeakType, frozenset[RootCause]] = {
    LeakType.FAILED_PAYMENT: frozenset(
        set(RootCause) - _RECEIVABLES_CAUSES
    ),
    LeakType.ABANDONED_CART: frozenset({
        RootCause.CART_ABANDONMENT,
        RootCause.UNKNOWN,
    }),
    LeakType.FAILED_MANDATE: frozenset({
        RootCause.MANDATE_REVOKED,
        RootCause.BANK_DOWNTIME,
        RootCause.INSUFFICIENT_FUNDS,
        RootCause.UNKNOWN,
    }),
    LeakType.OVERDUE_INVOICE: _RECEIVABLES_CAUSES | {RootCause.UNKNOWN},
}


class ReplyIntent(str, Enum):
    """What a customer said back, as a closed set.

    The second job the model does, and it is the same job as the first: produce
    a label. It does not decide what the label means — `brain/conversation`
    holds the deterministic table for that, exactly as `policy/` does for
    RootCause. A hallucinated intent is still one of these seven.
    """

    PROMISE_TO_PAY = "PROMISE_TO_PAY"
    DISPUTED = "DISPUTED"
    ALREADY_PAID = "ALREADY_PAID"
    WRONG_CONTACT = "WRONG_CONTACT"
    PARTIAL_PAYMENT_OFFER = "PARTIAL_PAYMENT_OFFER"
    STOP_CONTACTING = "STOP_CONTACTING"
    UNCLEAR = "UNCLEAR"


class PromiseState(str, Enum):
    OPEN = "OPEN"
    KEPT = "KEPT"
    BROKEN = "BROKEN"


class Channel(str, Enum):
    SMS = "SMS"
    EMAIL = "EMAIL"
    WHATSAPP = "WHATSAPP"
    VOICE = "VOICE"  # V2 — guardrails already apply, no implementation


class Stage(str, Enum):
    """Audit log stages. One record's timeline is a sequence of these."""

    DETECT = "DETECT"
    DIAGNOSE = "DIAGNOSE"
    DECIDE = "DECIDE"
    GUARDRAIL = "GUARDRAIL"
    EXECUTE = "EXECUTE"
    REPLY = "REPLY"
    OUTCOME = "OUTCOME"
