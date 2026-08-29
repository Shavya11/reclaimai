"""Layer 1: lookup table. Free, instant, confidence 1.0.

Keys are the literal strings Razorpay puts in `error.reason`, with `error.code`
as a coarser fallback. They are matched exactly and lowercased — a near-miss is
a silent miss, which is why `cli verify` checks this map against the fixture that
`cli harvest` writes rather than trusting it by eye.

The deliberate omission is the generic decline family — payment_failed,
payment_declined, card_declined. Those cover insufficient funds, a risk decline
and a daily-limit breach at once, so resolving them here would be guessing. They
fall through to layer 2 on purpose.
"""

from ...enums import RootCause
from ...models import AtRiskRecord, Diagnosis

DETERMINISTIC_MAP: dict[str, RootCause] = {
    # transient issuer / gateway problems
    "gateway_technical_error": RootCause.BANK_DOWNTIME,
    "bank_not_available": RootCause.BANK_DOWNTIME,
    "bank_technical_error": RootCause.BANK_DOWNTIME,
    "bank_cutoff_in_progress": RootCause.BANK_DOWNTIME,
    "payment_declined_due_to_high_traffic": RootCause.BANK_DOWNTIME,
    # the instrument itself is unusable
    "card_expired": RootCause.EXPIRED_INSTRUMENT,
    "incorrect_card_details": RootCause.INVALID_INSTRUMENT,
    "card_number_invalid": RootCause.INVALID_INSTRUMENT,
    "incorrect_card_expiry_date": RootCause.INVALID_INSTRUMENT,
    "incorrect_cvv": RootCause.INVALID_INSTRUMENT,
    "incorrect_cardholder_name": RootCause.INVALID_INSTRUMENT,
    "invalid_vpa": RootCause.INVALID_INSTRUMENT,
    "bank_account_invalid": RootCause.INVALID_INSTRUMENT,
    # the customer walked away mid-authentication
    "payment_cancelled": RootCause.AUTH_DROPOFF,
    "payment_pending": RootCause.AUTH_DROPOFF,
    "authentication_failed": RootCause.AUTH_DROPOFF,
    "incorrect_otp": RootCause.AUTH_DROPOFF,
    "otp_expired": RootCause.AUTH_DROPOFF,
    "otp_attempts_exceeded": RootCause.AUTH_DROPOFF,
    "payment_session_expired": RootCause.AUTH_DROPOFF,
    "payment_collect_request_expired": RootCause.AUTH_DROPOFF,
    # merchant or network policy forbids it — a human decision, never a retry
    "international_transaction_not_allowed": RootCause.POLICY_BLOCK,
    "payment_method_not_enabled": RootCause.POLICY_BLOCK,
    "bank_not_enabled": RootCause.POLICY_BLOCK,
    "card_network_not_enabled": RootCause.POLICY_BLOCK,
    "card_type_invalid": RootCause.POLICY_BLOCK,
    "invalid_currency": RootCause.POLICY_BLOCK,
    "user_not_registered_for_netbanking": RootCause.POLICY_BLOCK,
    # recurring mandates. Razorpay reports *revocation* through the subscription
    # entity's status, not through a payment error_reason — the reasons below are
    # the mandate failures that do reach a payment.
    "mandate_creation_failed": RootCause.MANDATE_REVOKED,
    "mandate_creation_declined": RootCause.MANDATE_REVOKED,
    "mandate_creation_expired": RootCause.MANDATE_REVOKED,
    "mandate_creation_timeout": RootCause.MANDATE_REVOKED,
    # our side
    "server_error": RootCause.TECHNICAL_ERROR,
    "invalid_request": RootCause.TECHNICAL_ERROR,
    "invalid_order_id": RootCause.TECHNICAL_ERROR,
    "invalid_amount": RootCause.TECHNICAL_ERROR,
    "invalid_response_from_gateway": RootCause.TECHNICAL_ERROR,
}

CODE_FALLBACK: dict[str, RootCause] = {
    "gateway_error": RootCause.BANK_DOWNTIME,
    "server_error": RootCause.TECHNICAL_ERROR,
}

# Present in the data, deliberately absent from the map above.
AMBIGUOUS_REASONS = frozenset({
    "payment_failed", "payment_declined", "card_declined", "debit_declined",
    "authorisation_declined_by_psp",
})


def diagnose(record: AtRiskRecord) -> Diagnosis | None:
    """Return a Diagnosis when the error names its own cause, else None so the
    caller falls through to layer 2. Never raises."""
    try:
        return _diagnose(record)
    except Exception:  # a broken lookup must not kill the batch
        return None


def _diagnose(record: AtRiskRecord) -> Diagnosis | None:
    from ...enums import LeakType

    # An overdue invoice has no error string. What it has is a ledger, and some
    # of what the ledger records is fact rather than inference.
    if record.leak_type is LeakType.OVERDUE_INVOICE:
        from .receivables import diagnose as diagnose_receivable

        return diagnose_receivable(record)

    # A cart with no payment attempt has no error to read, and needs none.
    if record.leak_type is LeakType.ABANDONED_CART:
        return Diagnosis(
            root_cause=RootCause.CART_ABANDONMENT, confidence=1.0,
            reasoning="Order created but no payment was ever attempted.",
            recoverable=True, evidence_used=["leak_type=ABANDONED_CART"],
            source="deterministic",
        )

    error = record.raw_signals.get("error") or {}
    reason = str(error.get("reason") or "").strip().lower()
    code = str(error.get("code") or "").strip().lower()

    if reason in AMBIGUOUS_REASONS:
        return None

    cause = DETERMINISTIC_MAP.get(reason) or CODE_FALLBACK.get(code)
    if cause is None:
        return None

    return Diagnosis(
        root_cause=cause, confidence=1.0,
        reasoning=f"Error reason '{reason or code}' maps to {cause.value} by lookup.",
        recoverable=cause not in {RootCause.MANDATE_REVOKED, RootCause.POLICY_BLOCK},
        evidence_used=[f"error.reason={reason}" if reason else f"error.code={code}"],
        source="deterministic",
    )


def coverage(records) -> float:
    """Share of records layer 1 resolves. PROJECT.md targets ~60%."""
    if not records:
        return 0.0
    return sum(1 for r in records if diagnose(r) is not None) / len(records)
