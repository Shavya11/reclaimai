"""Webhook signature verification.

HMAC-SHA256 of the RAW REQUEST BODY with the webhook secret, hex-encoded,
compared in constant time.

Two mistakes are easy here and both are fatal:

  * Verifying a re-serialized payload. `json.dumps(await request.json())` is not
    the bytes Razorpay signed — key order, separators and unicode escaping all
    differ — so the signature never matches, and the usual "fix" is to stop
    checking it. Verify the bytes off the wire, before anything parses them.
  * Comparing with `==`. String comparison short-circuits on the first differing
    byte, which leaks the correct prefix through timing. `compare_digest` does
    not.

An unverified webhook can mark a record RECOVERED and attribute money to it.
This function is the only thing standing between a stranger and the scoreboard.
"""

import hashlib
import hmac

SIGNATURE_HEADER = "X-Razorpay-Signature"
EVENT_ID_HEADER = "X-Razorpay-Event-Id"


class SignatureError(ValueError):
    """Raised only where a caller has asked to be told. `verify` returns a bool."""


def sign(raw_body: bytes, secret: str) -> str:
    """The signature Razorpay would send for this exact body. Used by the tests
    and by the outcome replay, so both exercise the real verification path
    instead of bypassing it."""
    if isinstance(raw_body, str):  # a str here is the bug this module exists for
        raw_body = raw_body.encode("utf-8")
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def verify(raw_body: bytes, signature: str | None, secret: str) -> bool:
    """True only if the body was signed with this secret. Every failure mode —
    no secret configured, no signature header, wrong length, wrong digest —
    returns False. There is no path through this function that accepts an
    unverified body."""
    if not secret or not signature:
        return False
    try:
        expected = sign(raw_body, secret)
    except (TypeError, AttributeError):
        return False
    return hmac.compare_digest(expected, signature.strip())


def verify_or_raise(raw_body: bytes, signature: str | None, secret: str) -> None:
    if not verify(raw_body, signature, secret):
        raise SignatureError("webhook signature verification failed")
