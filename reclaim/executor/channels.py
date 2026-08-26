"""Channel abstraction.

One send() for every channel. Guardrails sit ABOVE this layer, never inside it,
so adding a channel — V2's voice, for instance — inherits consent, DND, quiet
hours and the frequency cap for free rather than reimplementing them and getting
one of them subtly wrong.

Nothing here decides whether a message should be sent. By the time execution
reaches this module, that question has already been answered by the gate.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

from ..config import settings
from ..enums import Channel
from ..clock import now

log = logging.getLogger(__name__)


@dataclass
class Delivery:
    channel: Channel
    recipient: str
    message: str
    sent_at: datetime
    ok: bool = True
    provider_ref: str | None = None
    error: str | None = None


@dataclass
class ChannelSender:
    """In DRY_RUN, deliveries are recorded rather than sent. The transcript is
    what the tests assert against, so the send path is exercised either way."""

    dry_run: bool | None = None
    sent: list[Delivery] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.dry_run is None:
            self.dry_run = settings.dry_run

    def send(self, channel: Channel, recipient: str, message: str) -> Delivery:
        if not recipient:
            return self._record(Delivery(
                channel=channel, recipient="", message=message, sent_at=now(),
                ok=False, error="no recipient on file",
            ))

        delivery = Delivery(
            channel=channel, recipient=recipient, message=message, sent_at=now(),
        )

        if self.dry_run:
            log.info("DRY_RUN send %s -> %s", channel.value, recipient)
            delivery.provider_ref = f"dry_{channel.value.lower()}_{len(self.sent)}"
            return self._record(delivery)

        # Razorpay delivers the payment link over SMS and email on our behalf in
        # test mode, so V1 has no separate SMS/email vendor to call. A real
        # deployment swaps this branch for the vendor SDK; nothing above this
        # line changes.
        delivery.provider_ref = f"rzp_notify_{len(self.sent)}"
        return self._record(delivery)

    def _record(self, delivery: Delivery) -> Delivery:
        self.sent.append(delivery)
        return delivery


def recipient_for(channel: Channel, customer) -> str:
    """Email for EMAIL, phone for everything else. A missing contact detail is a
    failed delivery, not a crash."""
    if customer is None:
        return ""
    if channel is Channel.EMAIL:
        return getattr(customer, "email", "") or ""
    return getattr(customer, "phone", "") or ""
