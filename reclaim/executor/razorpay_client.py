"""Thin Razorpay wrapper. Three responsibilities and nothing else:

  * every write carries an idempotency key
  * transient failures retry with backoff, permanent ones do not
  * DRY_RUN logs the call instead of making it, so a clone with no credentials
    still runs the full pipeline end to end
"""

import logging
import random
import time
from typing import Any, Callable

from ..config import settings

log = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_BASE_DELAY = 0.5

# Razorpay returns these when the request itself was fine and the world was not.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class RazorpayError(RuntimeError):
    pass


class RazorpayClient:
    def __init__(self, dry_run: bool | None = None) -> None:
        self.dry_run = settings.dry_run if dry_run is None else dry_run
        self._client = None
        self.calls: list[dict[str, Any]] = []  # DRY_RUN transcript, used by tests
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
        return self._write(
            "order.create",
            idempotency_key,
            lambda: self._client.order.create(
                {"amount": amount, "currency": "INR", **kw}
            ),
            stub={"id": f"order_stub_{idempotency_key[-8:]}", "amount": amount,
                  "status": "created"},
        )

    def create_payment_link(
        self, amount: int, *, idempotency_key: str, prefill_method: str | None = None, **kw
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"amount": amount, "currency": "INR", **kw}
        if prefill_method:
            payload["options"] = {"checkout": {"method": {prefill_method: "1"}}}
        return self._write(
            "payment_link.create",
            idempotency_key,
            lambda: self._client.payment_link.create(payload),
            stub={"id": f"plink_stub_{idempotency_key[-8:]}", "amount": amount,
                  "short_url": f"https://rzp.io/i/stub{idempotency_key[-6:]}",
                  "status": "created"},
        )

    # -- plumbing ------------------------------------------------------------

    def _write(
        self, op: str, idempotency_key: str, call: Callable[[], Any], *, stub: dict
    ) -> dict[str, Any]:
        if not idempotency_key:
            raise RazorpayError(f"{op} attempted without an idempotency key")
        self.calls.append({"op": op, "idempotency_key": idempotency_key})
        if self.dry_run:
            log.info("DRY_RUN %s key=%s", op, idempotency_key)
            return {**stub, "_dry_run": True}
        return self._with_retry(op, call)

    def _with_retry(self, op: str, call: Callable[[], Any]) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return call()
            except Exception as exc:  # noqa: BLE001 - SDK raises a wide surface
                last = exc
                if not self._is_retryable(exc) or attempt == _MAX_ATTEMPTS:
                    break
                delay = _BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 0.2)
                log.warning("%s failed (attempt %d), retrying in %.2fs", op, attempt, delay)
                time.sleep(delay)
        raise RazorpayError(f"{op} failed after retries: {last}") from last

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if isinstance(code, int):
            return code in _RETRYABLE_STATUS
        return isinstance(exc, (TimeoutError, ConnectionError))
