"""Thin Razorpay wrapper. Three responsibilities and nothing else:

  * every write carries its idempotency key TO Razorpay - as `receipt` on an
    order and `reference_id` on a payment link - and a retry looks the key up
    before it sends again
  * transient failures retry with backoff, permanent ones do not
  * DRY_RUN logs the call instead of making it, so a clone with no credentials
    still runs the full pipeline end to end

The first point is the one that used to be false. The key was claimed locally
before the call, which stops the SAME tuple executing twice - but nothing
carried it onto the wire, so a request that succeeded at Razorpay and timed out
on the way back was re-sent with nothing for Razorpay to deduplicate on. Three
attempts, three orders. Razorpay enforces `reference_id` unique per payment
link and lets an order be fetched by `receipt`, which is what the retry now
does: the second attempt asks whether the first one landed.
"""

import hashlib
import logging
import random
import time
from typing import Any, Callable

from ..config import settings

log = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_BASE_DELAY = 0.5
_MAX_KEY_LENGTH = 40  # Razorpay's cap on both `receipt` and `reference_id`
_UNKNOWN = object()   # a lookup that errored, as distinct from one that found nothing

# Razorpay returns these when the request itself was fine and the world was not.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_RETRYABLE_MESSAGES = ("too many requests", "rate limit", "timeout", "gateway")


class RazorpayError(RuntimeError):
    pass


def _first(listing: Any) -> dict[str, Any] | None:
    """The one entity in a Razorpay list response, or None.

    Orders come back as `{"items": [...]}`; payment links as
    `{"payment_links": [...]}`. Same question, two shapes.
    """
    if not isinstance(listing, dict):
        return None
    items = listing.get("items") or listing.get("payment_links") or []
    return items[0] if items else None


def _stub_id(idempotency_key: str) -> str:
    """A DRY_RUN id must be as unique as the real one it stands in for.

    Slicing the key (`key[-8:]`) looks unique and is not: every SEND_LINK ends
    in the same eight characters, so every stubbed link came back with the same
    id and outcome attribution walked all of them to one intervention. A digest
    of the whole key collides only if the keys do.
    """
    return hashlib.sha1(idempotency_key.encode("utf-8")).hexdigest()[:14]


class RazorpayClient:
    def __init__(self, dry_run: bool | None = None, *, sdk: Any = None) -> None:
        self.dry_run = settings.dry_run if dry_run is None else dry_run
        self._client = None
        self.calls: list[dict[str, Any]] = []  # DRY_RUN transcript, used by tests
        if sdk is not None:
            # A stand-in for razorpay.Client. Exists so a test can assert what
            # actually goes on the wire; the DRY_RUN transcript records the key
            # this wrapper was GIVEN, which is not the same claim.
            self._client = sdk
            self.dry_run = False
            return
        if not self.dry_run:
            if not settings.has_razorpay:
                raise RazorpayError("live mode requested but no rzp_test_ credentials")
            import razorpay

            self._client = razorpay.Client(
                auth=(settings.razorpay_key_id, settings.razorpay_key_secret)
            )
            self._client.set_app_details({"title": "ReclaimAI", "version": "1.0"})

    # -- writes --------------------------------------------------------------

    def create_order(self, amount: int, *, idempotency_key: str, **kw) -> dict[str, Any]:
        # `receipt` is Razorpay's merchant reference on an order, and the one
        # field an order can be fetched back by.
        payload = {"amount": amount, "currency": "INR",
                   "receipt": idempotency_key, **kw}
        return self._write(
            "order.create",
            idempotency_key,
            lambda: self._client.order.create(payload),
            find=lambda: _first(self._client.order.all({"receipt": idempotency_key})),
            stub={"id": f"order_stub_{_stub_id(idempotency_key)}", "amount": amount,
                  "receipt": idempotency_key, "status": "created"},
        )

    def create_payment_link(
        self, amount: int, *, idempotency_key: str, prefill_method: str | None = None, **kw
    ) -> dict[str, Any]:
        # `reference_id` is enforced unique per payment link by Razorpay, so a
        # duplicate is refused server-side - the remote half of the guarantee.
        payload: dict[str, Any] = {"amount": amount, "currency": "INR",
                                   "reference_id": idempotency_key, **kw}
        if prefill_method:
            payload["options"] = {"checkout": {"method": {prefill_method: "1"}}}
        return self._write(
            "payment_link.create",
            idempotency_key,
            lambda: self._client.payment_link.create(payload),
            find=lambda: _first(self._client.payment_link.all(
                {"reference_id": idempotency_key})),
            stub={"id": f"plink_stub_{_stub_id(idempotency_key)}", "amount": amount,
                  "reference_id": idempotency_key,
                  "short_url": f"https://rzp.io/i/{_stub_id(idempotency_key)[:8]}",
                  "status": "created"},
        )

    # -- reads ---------------------------------------------------------------
    # No idempotency key: fetching twice changes nothing. DRY_RUN has no remote
    # to read from, so a read there is a mistake rather than something to stub.

    def fetch_payment(self, payment_id: str) -> dict[str, Any]:
        return self._read("payment.fetch", lambda: self._client.payment.fetch(payment_id))

    def fetch_order(self, order_id: str) -> dict[str, Any]:
        return self._read("order.fetch", lambda: self._client.order.fetch(order_id))

    def _read(self, op: str, call: Callable[[], Any]) -> dict[str, Any]:
        if self.dry_run or self._client is None:
            raise RazorpayError(f"{op} needs live rzp_test_ credentials")
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 - SDK raises a wide surface
            raise RazorpayError(f"{op} failed: {exc}") from exc

    # -- plumbing ------------------------------------------------------------

    def _write(
        self, op: str, idempotency_key: str, call: Callable[[], Any], *,
        find: Callable[[], Any], stub: dict,
    ) -> dict[str, Any]:
        if not idempotency_key:
            raise RazorpayError(f"{op} attempted without an idempotency key")
        if len(idempotency_key) > _MAX_KEY_LENGTH:
            # Both `receipt` and `reference_id` cap at 40 characters. A key that
            # does not fit would be silently truncated or refused; either way the
            # guarantee is gone, so refuse here where it is visible.
            raise RazorpayError(f"{op} idempotency key exceeds {_MAX_KEY_LENGTH} chars")
        self.calls.append({"op": op, "idempotency_key": idempotency_key})
        if self.dry_run:
            log.info("DRY_RUN %s key=%s", op, idempotency_key)
            return {**stub, "_dry_run": True}
        return self._with_retry(op, call, find)

    def _with_retry(
        self, op: str, call: Callable[[], Any], find: Callable[[], Any]
    ) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            if attempt > 1:
                # The previous attempt may have succeeded at Razorpay and failed
                # only on the way back. Ask before sending again.
                existing = self._lookup(op, find)
                if existing is _UNKNOWN:
                    # Cannot tell whether the last request landed. Re-sending
                    # risks a second charge; stopping risks an unsent link. A
                    # payments system takes the second risk, every time.
                    raise RazorpayError(
                        f"{op}: previous attempt may have landed and the lookup "
                        f"failed; refusing to re-send. Last error: {last}"
                    ) from last
                if existing:
                    log.info("%s already landed for this key; not re-sending", op)
                    return existing
            try:
                return call()
            except Exception as exc:  # noqa: BLE001 - SDK raises a wide surface
                last = exc
                if self._is_duplicate(exc):
                    # Razorpay refused because the key already exists - the
                    # remote guard fired. Fetch what it is guarding.
                    existing = self._lookup(op, find)
                    if existing and existing is not _UNKNOWN:
                        return existing
                if not self._is_retryable(exc) or attempt == _MAX_ATTEMPTS:
                    break
                delay = _BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 0.2)
                log.warning("%s failed (attempt %d), retrying in %.2fs", op, attempt, delay)
                time.sleep(delay)
        raise RazorpayError(f"{op} failed after retries: {last}") from last

    @staticmethod
    def _lookup(op: str, find: Callable[[], Any]) -> Any:
        """The original entity, None if nothing landed, or _UNKNOWN if the
        question could not be answered. Three answers, because "I don't know"
        and "no" lead to opposite decisions."""
        try:
            return find() or None
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: lookup by idempotency key failed: %s", op, exc)
            return _UNKNOWN

    @staticmethod
    def _is_duplicate(exc: Exception) -> bool:
        return "already exist" in str(exc).lower()

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if isinstance(code, int):
            return code in _RETRYABLE_STATUS
        # Razorpay surfaces throttling as a plain BadRequestError with no status
        # code, so the message is the only signal available.
        if any(s in str(exc).lower() for s in _RETRYABLE_MESSAGES):
            return True
        return isinstance(exc, (TimeoutError, ConnectionError))


class DeadRazorpayClient(RazorpayClient):
    """Razorpay, unreachable. Demo beat #6.

    Pre-staged rather than improvised: killing the integration on stage by
    editing code is a good way to discover that the failure path was never
    exercised. Every write raises, and the batch is expected to finish anyway -
    records park for human review, no key is claimed twice, nothing crashes.
    """

    def __init__(self, *_args, **_kwargs) -> None:
        super().__init__(dry_run=True)

    def create_order(self, *_args, **_kwargs):
        raise RazorpayError("connection refused (simulated outage)")

    def create_payment_link(self, *_args, **_kwargs):
        raise RazorpayError("connection refused (simulated outage)")
