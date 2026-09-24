"""Razorpay webhook payload -> a flat event this system can act on.

Razorpay nests differently per event: a paid link carries the amount on
`payload.payment_link.entity.amount_paid`, a captured payment on
`payload.payment.entity.amount`. Normalising that here means the attribution
code deals with one shape and stays readable.

Nothing in this module trusts the payload's structure. A webhook is input from
the network; a missing key is a malformed event, not a KeyError.
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

# The five events that close the loop. Anything else is acknowledged and
# ignored — Razorpay lets you subscribe broadly, and 200-then-ignore is the
# correct response to an event we have no opinion about.
SUCCESS_EVENTS = frozenset({
    "payment.captured",
    "payment_link.paid",
    "order.paid",
    "subscription.charged",
})
FAILURE_EVENTS = frozenset({"payment.failed"})
HANDLED_EVENTS = SUCCESS_EVENTS | FAILURE_EVENTS


class MalformedEvent(ValueError):
    pass


@dataclass(frozen=True)
class WebhookEvent:
    event_id: str
    event_type: str
    refs: tuple[str, ...]          # candidate razorpay ids, most specific first
    record_id: str | None          # from notes — the executor puts it there
    amount: int                    # paise
    succeeded: bool
    error: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def handled(self) -> bool:
        return self.event_type in HANDLED_EVENTS


def _entity(payload: dict, name: str) -> dict:
    node = payload.get(name)
    if isinstance(node, dict):
        inner = node.get("entity")
        if isinstance(inner, dict):
            return inner
    return {}


def _first_int(*values) -> int:
    for v in values:
        if isinstance(v, int) and v > 0:
            return v
    return 0


def derive_event_id(body: dict) -> str:
    """Razorpay sends X-Razorpay-Event-Id, but a webhook with no header must
    still deduplicate. Hashing the body gives a stable id for identical
    deliveries, which is exactly the retry case dedup exists for."""
    blob = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "evt_sha_" + hashlib.sha256(blob).hexdigest()[:24]


def parse(body: dict[str, Any], *, event_id: str | None = None) -> WebhookEvent:
    if not isinstance(body, dict):
        raise MalformedEvent("webhook body is not an object")

    event_type = body.get("event")
    if not isinstance(event_type, str) or not event_type:
        raise MalformedEvent("webhook body has no event type")

    payload = body.get("payload")
    if not isinstance(payload, dict):
        payload = {}

    payment = _entity(payload, "payment")
    link = _entity(payload, "payment_link")
    order = _entity(payload, "order")
    subscription = _entity(payload, "subscription")

    # Most specific first: a paid link is attributable to the link we created,
    # and only then to the order or payment underneath it.
    refs = tuple(r for r in (
        link.get("id"), subscription.get("id"), order.get("id"),
        payment.get("order_id"), payment.get("id"),
    ) if isinstance(r, str) and r)

    record_id = None
    for entity in (link, subscription, order, payment):
        notes = entity.get("notes")
        if isinstance(notes, dict) and notes.get("record_id"):
            record_id = str(notes["record_id"])
            break

    amount = _first_int(
        link.get("amount_paid"), order.get("amount_paid"),
        payment.get("amount"), link.get("amount"), order.get("amount"),
    )

    error = None
    if payment.get("error_code") or payment.get("error_description"):
        error = {
            "code": payment.get("error_code"),
            "description": payment.get("error_description"),
            "source": payment.get("error_source"),
            "step": payment.get("error_step"),
            "reason": payment.get("error_reason"),
        }

    return WebhookEvent(
        event_id=event_id or derive_event_id(body),
        event_type=event_type,
        refs=refs,
        record_id=record_id,
        amount=amount,
        succeeded=event_type in SUCCESS_EVENTS,
        error=error,
        raw=body,
    )
