"""Layer 1: lookup table. Free, instant, confidence 1.0.

Keys are the literal strings Razorpay puts in `error.reason`, with `error.code`
as a coarser fallback. They are matched exactly and lowercased — a near-miss is
a silent miss, which is why `cli verify` checks this map against the fixture that
`cli harvest` writes rather than trusting it by eye.

The deliberate omission is the generic decline family — payment_failed,
payment_declined_by_bank. Those cover insufficient funds, a risk decline and a
daily-limit breach at once, so resolving them here would be guessing. They fall
through to layer 2 on purpose.
"""

from ...enums import RootCause
from ...models import AtRiskRecord, Diagnosis

DETERMINISTIC_MAP: dict[str, RootCause] = {
    # transient issuer / gateway problems
    "gateway_technical_error": RootCause.BANK_DOWNTIME,
    "gateway_error": RootCause.BANK_DOWNTIME,
    "bank_downtime": RootCause.BANK_DOWNTIME,
    "issuer_down": RootCause.BANK_DOWNTIME,
    # the instrument itself is unusable
    "card_expired": RootCause.EXPIRED_INSTRUMENT,
    "invalid_card_number": RootCause.INVALID_INSTRUMENT,
    "invalid_card_expiry": RootCause.INVALID_INSTRUMENT,
    "incorrect_card_details": RootCause.INVALID_INSTRUMENT,
    "invalid_cvv": RootCause.INVALID_INSTRUMENT,
    # the customer walked away mid-authentication
    "payment_delayed_by_user": RootCause.AUTH_DROPOFF,
    "payment_cancelled": RootCause.AUTH_DROPOFF,
    "payment_pending": RootCause.AUTH_DROPOFF,
    "otp_incorrect_or_expired": RootCause.AUTH_DROPOFF,
    "3ds_authentication_failed": RootCause.AUTH_DROPOFF,
    # merchant or network policy forbids it — a human decision, never a retry
    "international_transaction_not_allowed": RootCause.POLICY_BLOCK,
    "payment_method_not_enabled": RootCause.POLICY_BLOCK,
    "card_not_supported": RootCause.POLICY_BLOCK,
    # recurring mandates
    "mandate_revoked": RootCause.MANDATE_REVOKED,
    "mandate_cancelled": RootCause.MANDATE_REVOKED,
    "subscription_cancelled": RootCause.MANDATE_REVOKED,
    # our side
    "server_error": RootCause.TECHNICAL_ERROR,
    "invalid_request_error": RootCause.TECHNICAL_ERROR,
}

CODE_FALLBACK: dict[str, RootCause] = {
    "gateway_error": RootCause.BANK_DOWNTIME,
    "server_error": RootCause.TECHNICAL_ERROR,
}

# Present in the data, deliberately absent from the map above.
AMBIGUOUS_REASONS = frozenset({
    "payment_failed", "payment_declined_by_bank", "declined_by_bank",
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
