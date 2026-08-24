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
    OUTCOME = "OUTCOME"
