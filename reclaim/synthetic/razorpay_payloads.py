"""Razorpay webhook payloads, in Razorpay's real shape.

These are fixtures of the wire format — the nesting, the key names, the paise
amounts, the notes dict — not a convenient shape invented for our own
convenience. The outcome replay signs one of these and posts it through the
same receiver a live Razorpay delivery hits, which is the only way the
attribution chain gets exercised without a public tunnel.

If Razorpay's shape ever changes, this file is the one place that is wrong.
"""

import time
from typing import Any

ACCOUNT_ID = "acc_ReclaimTestMerchant"


def _envelope(event: str, contains: list[str], payload: dict[str, Any],
              created_at: int | None = None) -> dict[str, Any]:
    return {
        "entity": "event",
        "account_id": ACCOUNT_ID,
        "event": event,
        "contains": contains,
        "payload": payload,
        "created_at": created_at or int(time.time()),
    }


def payment_link_paid(
    *, link_id: str, payment_id: str, amount: int, record_id: str,
    attempt: int = 1, created_at: int | None = None,
) -> dict[str, Any]:
    notes = {"record_id": record_id, "attempt": str(attempt)}
    return _envelope(
        "payment_link.paid", ["payment_link", "payment"],
        {
            "payment_link": {"entity": {
                "id": link_id, "entity": "payment_link", "status": "paid",
                "amount": amount, "amount_paid": amount, "currency": "INR",
                "notes": notes,
            }},
            "payment": {"entity": {
                "id": payment_id, "entity": "payment", "status": "captured",
                "amount": amount, "currency": "INR", "method": "upi",
                "captured": True, "notes": notes,
            }},
        },
        created_at,
    )


def order_paid(
    *, order_id: str, payment_id: str, amount: int, record_id: str,
    created_at: int | None = None,
) -> dict[str, Any]:
    notes = {"record_id": record_id, "reclaim": "silent_retry"}
    return _envelope(
        "order.paid", ["order", "payment"],
        {
            "order": {"entity": {
                "id": order_id, "entity": "order", "status": "paid",
                "amount": amount, "amount_paid": amount, "currency": "INR",
                "notes": notes,
            }},
            "payment": {"entity": {
                "id": payment_id, "entity": "payment", "status": "captured",
                "amount": amount, "currency": "INR", "method": "card",
                "order_id": order_id, "captured": True, "notes": notes,
            }},
        },
        created_at,
    )


def payment_captured(
    *, payment_id: str, amount: int, record_id: str, order_id: str | None = None,
    created_at: int | None = None,
) -> dict[str, Any]:
    return _envelope(
        "payment.captured", ["payment"],
        {"payment": {"entity": {
            "id": payment_id, "entity": "payment", "status": "captured",
            "amount": amount, "currency": "INR", "method": "upi",
            "order_id": order_id, "captured": True,
            "notes": {"record_id": record_id},
        }}},
        created_at,
    )


def payment_captured_unprompted(
    *, payment_id: str, amount: int, order_id: str,
    created_at: int | None = None,
) -> dict[str, Any]:
    """A customer paying on their own, against the merchant's original order.

    Deliberately carries NO `notes.record_id`. Our executor writes that note on
    every link and order it mints, so the note is the marker that says "this is
    ours" — forging it on a payment we did not cause would make an unprompted
    payment indistinguishable from a recovery, which is the exact confusion the
    self-cure baseline exists to prevent.

    What it carries instead is the order id the merchant already had. That is
    genuinely how this arrives: the customer pays the original invoice, not the
    link we sent, and the only thing tying it to a record is a reference we
    never controlled.
    """
    return _envelope(
        "payment.captured", ["payment"],
        {"payment": {"entity": {
            "id": payment_id, "entity": "payment", "status": "captured",
            "amount": amount, "currency": "INR", "method": "upi",
            "order_id": order_id, "captured": True,
            "notes": {},
        }}},
        created_at,
    )


def subscription_charged(
    *, subscription_id: str, payment_id: str, amount: int, record_id: str,
    created_at: int | None = None,
) -> dict[str, Any]:
    notes = {"record_id": record_id}
    return _envelope(
        "subscription.charged", ["subscription", "payment"],
        {
            "subscription": {"entity": {
                "id": subscription_id, "entity": "subscription",
                "status": "active", "notes": notes,
            }},
            "payment": {"entity": {
                "id": payment_id, "entity": "payment", "status": "captured",
                "amount": amount, "currency": "INR", "notes": notes,
            }},
        },
        created_at,
    )


def payment_failed(
    *, payment_id: str, amount: int, record_id: str, order_id: str | None = None,
    code: str = "BAD_REQUEST_ERROR", description: str = "Payment failed",
    reason: str = "payment_failed", step: str = "payment_authorization",
    source: str = "bank", created_at: int | None = None,
) -> dict[str, Any]:
    return _envelope(
        "payment.failed", ["payment"],
        {"payment": {"entity": {
            "id": payment_id, "entity": "payment", "status": "failed",
            "amount": amount, "currency": "INR", "method": "card",
            "order_id": order_id, "captured": False,
            "error_code": code, "error_description": description,
            "error_source": source, "error_step": step, "error_reason": reason,
            "notes": {"record_id": record_id},
        }}},
        created_at,
    )
