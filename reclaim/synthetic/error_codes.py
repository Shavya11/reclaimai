"""Razorpay-shaped error payloads, grouped by how much they actually reveal.

The split matters more than the contents. AMBIGUOUS entries are the generic
"declined by the bank" family that covers insufficient funds, a risk decline and
a daily-limit breach all at once — four causes, four different correct responses,
one indistinguishable error string. That gap is where the LLM earns its place;
everything in SPECIFIC is a lookup table's job.
"""

# Errors whose reason string names the cause outright -> deterministic layer.
SPECIFIC: dict[str, dict[str, str]] = {
    "BANK_DOWNTIME": {
        "code": "GATEWAY_ERROR", "reason": "gateway_technical_error",
        "source": "gateway", "step": "payment_authorization",
        "description": "Payment processing failed at the bank's end.",
    },
    "EXPIRED_INSTRUMENT": {
        "code": "BAD_REQUEST_ERROR", "reason": "card_expired",
        "source": "customer", "step": "payment_initiation",
        "description": "Your card has expired. Please use a different card.",
    },
    "INVALID_INSTRUMENT": {
        "code": "BAD_REQUEST_ERROR", "reason": "card_number_invalid",
        "source": "customer", "step": "payment_initiation",
        "description": "The card number entered is invalid.",
    },
    "AUTH_DROPOFF": {
        "code": "BAD_REQUEST_ERROR", "reason": "authentication_failed",
        "source": "customer", "step": "payment_authentication",
        "description": "Payment was not completed on the bank's OTP page.",
    },
    "POLICY_BLOCK": {
        "code": "BAD_REQUEST_ERROR", "reason": "international_transaction_not_allowed",
        "source": "business", "step": "payment_authorization",
        "description": "International cards are not supported for this merchant.",
    },
    "MANDATE_REVOKED": {
        "code": "BAD_REQUEST_ERROR", "reason": "mandate_creation_failed",
        "source": "customer", "step": "payment_authorization",
        "description": "The auto-debit mandate has been cancelled by the customer.",
    },
    "TECHNICAL_ERROR": {
        "code": "SERVER_ERROR", "reason": "server_error",
        "source": "internal", "step": "payment_initiation",
        "description": "An internal error occurred while creating the payment.",
    },
}

# Indistinguishable on the error alone. Resolved only by customer history,
# cohort signal, amount and timing.
AMBIGUOUS: list[dict[str, str]] = [
    {
        "code": "BAD_REQUEST_ERROR", "reason": "payment_failed",
        "source": "bank", "step": "payment_authorization",
        "description": "Your payment was declined by the bank.",
    },
    {
        "code": "BAD_REQUEST_ERROR", "reason": "payment_failed",
        "source": "bank", "step": "payment_authorization",
        "description": "The bank declined this transaction.",
    },
    {
        "code": "BAD_REQUEST_ERROR", "reason": "payment_declined",
        "source": "bank", "step": "payment_authorization",
        "description": "Transaction declined. Please contact your bank.",
    },
]

ISSUERS = ["HDFC", "ICICI", "SBIN", "AXIS", "KOTAK", "PNB", "YESB", "IDFC"]
NETWORKS = ["VISA", "MasterCard", "RuPay", "Amex"]
METHODS = ["card", "upi", "netbanking", "wallet"]
