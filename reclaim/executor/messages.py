"""Customer-facing copy.

The model MAY write this text. It may NOT choose the action, the amount, the
timing or the recipient — those come from the policy table, which is
deterministic. Text is the only place a model's output reaches a customer, and
even here a template is the fallback so the batch never depends on it.

Tone is a policy field, not a model decision. A first contact is gentle; a
later one is firmer. Nothing here ever threatens, because a failed card is
overwhelmingly a timing problem rather than a refusal to pay.
"""

from ..enums import Channel, RootCause
from ..money import format_inr

# (cause, tone) -> template. {amount}, {link} and {merchant} are substituted.
TEMPLATES: dict[tuple[RootCause, str], str] = {
    (RootCause.INSUFFICIENT_FUNDS, "gentle"): (
        "Hi! Your {amount} payment to {merchant} didn't go through. "
        "No rush — you can complete it here whenever suits: {link}"
    ),
    (RootCause.INSUFFICIENT_FUNDS, "firm"): (
        "Reminder: your {amount} payment to {merchant} is still pending. "
        "Complete it here: {link}"
    ),
    (RootCause.EXPIRED_INSTRUMENT, "neutral"): (
        "Your card on file has expired, so your {amount} payment to {merchant} "
        "couldn't be processed. You can pay by UPI in a few seconds here: {link}"
    ),
    (RootCause.INVALID_INSTRUMENT, "neutral"): (
        "We couldn't process your {amount} payment to {merchant} with the saved "
        "card details. Here's a quick link to pay another way: {link}"
    ),
    (RootCause.AUTH_DROPOFF, "gentle"): (
        "Looks like the bank verification step didn't finish for your {amount} "
        "payment to {merchant}. UPI skips that step entirely: {link}"
    ),
    (RootCause.CART_ABANDONMENT, "gentle"): (
        "You left {amount} in your cart at {merchant}. "
        "Still want it? Complete your order here: {link}"
    ),
}

FALLBACK = (
    "Your {amount} payment to {merchant} is still pending. "
    "You can complete it here: {link}"
)


def render(
    cause: RootCause,
    *,
    amount: int,
    link: str,
    tone: str = "gentle",
    merchant: str = "your merchant",
) -> str:
    template = TEMPLATES.get((cause, tone)) or FALLBACK
    return template.format(
        amount=format_inr(amount), link=link, merchant=merchant
    )


# SMS is metered and DLT-templated in India; long copy costs real money and
# risks truncation mid-link.
MAX_LENGTH: dict[Channel, int] = {
    Channel.SMS: 320,
    Channel.WHATSAPP: 1024,
    Channel.EMAIL: 4096,
    Channel.VOICE: 600,
}


def fits(channel: Channel, text: str) -> bool:
    return len(text) <= MAX_LENGTH.get(channel, 1024)
