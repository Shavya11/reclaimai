"""Simulated inbound replies.

Same honesty line as the rest of the fixture: the *reply* is modelled, and
everything downstream of it is production code. The text below is fed to the
real extractor, which makes a real model call, whose label passes through the
real deterministic handler, the real date validation and the real guardrail. The
only invented thing is that a customer answered at all — the same judgement
PROJECT.md §10 already discloses as modelled for whether they paid.

The Hinglish is not decoration. It is the single cheapest way to show that the
conversation layer handles how Indian customers actually write, and it is what
makes the voice roadmap an implementation detail rather than a research project:
the hard part of a Hinglish voice agent is understanding "sir friday tak ho
jayega", and that part is here, tested, over text.

Dates are deliberately RELATIVE - "friday", "agle hafte", "1 tarikh". Resolving
them is the model's job. Whether the resolved date is one the system will act on
is not: promises.validate_date decides that, and a reply naming a date six
months out is refused however confidently the model read it.
"""

import random

from ..enums import RootCause

# How often a customer answers at all, by what is actually wrong. A buyer who is
# short of cash answers far more often than one who is disputing quietly, and a
# consumer whose card expired mostly does not answer at all.
REPLY_RATE: dict[RootCause, float] = {
    RootCause.BUYER_CASH_CRUNCH: 0.75,
    RootCause.AWAITING_APPROVAL: 0.70,
    RootCause.INVOICE_DISPUTED: 0.65,
    RootCause.INVOICE_NOT_RECEIVED: 0.55,
    RootCause.PAYMENT_STALLED: 0.35,
    RootCause.INSUFFICIENT_FUNDS: 0.22,
    RootCause.EXPIRED_INSTRUMENT: 0.10,
    RootCause.AUTH_DROPOFF: 0.08,
    RootCause.CART_ABANDONMENT: 0.05,
}

DEFAULT_REPLY_RATE = 0.05

# Written the way these actually arrive: lower case, no punctuation, mixed
# script, occasionally rude. A corpus of well-formed English sentences would
# prove nothing about a system meant to run in India.
TEMPLATES: dict[RootCause, list[str]] = {
    RootCause.BUYER_CASH_CRUNCH: [
        "sir abhi funds nahi hai, friday tak clear kar denge",
        "payment agle hafte tak ho jayega, thoda time dijiye",
        "collection slow chal raha hai. 1 tarikh ko full payment bhej denge",
        "can we do 50% now and rest next month?",
        "we are facing a cash crunch this quarter, part payment possible hai?",
        "next friday tak definitely release kar denge, promise",
    ],
    RootCause.AWAITING_APPROVAL: [
        "invoice approval me hai, director sign karenge to release ho jayega",
        "our AP cycle runs on the 25th, payment will go out then",
        "finance team ne process kar diya hai, agle hafte credit ho jayega",
        "waiting for internal approval, should clear by month end",
        "PO approved ho gaya hai, payment friday ko release hoga",
    ],
    RootCause.INVOICE_NOT_RECEIVED: [
        "humein invoice mila hi nahi, dobara bhejiye",
        "we never received this invoice, please resend to accounts",
        "kaunsa invoice? hamare record me nahi hai",
        "please share the invoice copy, we will process by friday",
        "resend on accounts email, payment agle week kar denge",
    ],
    RootCause.INVOICE_DISPUTED: [
        "amount galat hai, humne 2 lakh ka order kiya tha",
        "material short aaya tha, credit note pending hai aapke taraf se",
        "PO number match nahi kar raha, ye invoice hamara nahi hai",
        "we never ordered this. please check your records",
        "GST number galat hai invoice me, revised invoice bhejiye",
    ],
    RootCause.PAYMENT_STALLED: [
        "dekh lete hai",
        "ok",
        "kis cheez ka payment?",
        "will check with accounts and revert",
        "mat bhejo baar baar, pareshan kar diya hai",
        "stop sending these messages",
        "payment kar diya tha na? UTR check kijiye",
        "monday tak kar denge",
    ],
    RootCause.INSUFFICIENT_FUNDS: [
        "salary aane do 1 tarikh ko, kar dunga",
        "abhi balance nahi hai bhai, agle hafte",
        "sorry, will pay after salary credit",
        "kar dunga tension mat lo",
    ],
    RootCause.EXPIRED_INSTRUMENT: [
        "card expire ho gaya hai, naya card se kar dunga",
        "will pay by upi instead",
    ],
    RootCause.AUTH_DROPOFF: [
        "otp nahi aaya tha, dobara try karunga",
        "payment page hang ho gaya tha",
    ],
    RootCause.CART_ABANDONMENT: [
        "abhi nahi chahiye",
        "price zyada lag raha hai",
    ],
}


def draw(record_id: str, cause: RootCause, *, seed: int = 42,
         attempt: int = 1) -> str | None:
    """The reply to one contact, or None for silence.

    Keyed on (record, attempt) like every other coin flip in the fixture, so a
    reply is reproducible and so the naive baseline — which never reads replies —
    draws from the same stream and differs only by strategy, never by luck.

    Silence is the common case and is returned as None rather than an empty
    string, because "no reply" and "an empty reply" reach different code and
    only one of them is real.
    """
    rng = random.Random(f"reply:{seed}:{record_id}:{attempt}")
    if rng.random() >= REPLY_RATE.get(cause, DEFAULT_REPLY_RATE):
        return None
    pool = TEMPLATES.get(cause)
    if not pool:
        return None
    return rng.choice(pool)
