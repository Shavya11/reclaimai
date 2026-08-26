"""The webhook front door.

Order matters and is enforced here rather than left to each caller:

    raw bytes -> verify signature -> parse JSON -> attribute

Parsing before verifying means running a stranger's JSON through your object
graph and *then* deciding whether to trust it. Verify first, on the bytes as
they arrived.

Every outcome returns a 2xx to Razorpay except a failed signature. A 500 on a
duplicate or an unattributable event just buys another delivery of an event we
already understood.
"""

import json
import logging
from dataclasses import dataclass

from .. import audit
from ..config import settings
from ..enums import Stage
from .attribution import MALFORMED, ORPHAN, Attribution, handle
from .signature import verify

log = logging.getLogger(__name__)


@dataclass
class Reception:
    status: int
    outcome: str
    attribution: Attribution | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status < 400

    def as_dict(self) -> dict:
        payload = {"status": self.status, "outcome": self.outcome}
        if self.attribution is not None:
            payload["attribution"] = self.attribution.as_dict()
        if self.error:
            payload["error"] = self.error
        return payload


def receive(
    raw_body: bytes,
    signature: str | None,
    *,
    event_id: str | None = None,
    secret: str | None = None,
    simulated: bool = False,
) -> Reception:
    secret = settings.webhook_secret if secret is None else secret

    if not verify(raw_body, signature, secret):
        # Worth auditing. An unsigned delivery is either a misconfiguration or
        # somebody trying to write to the scoreboard from outside.
        audit.log(ORPHAN, Stage.OUTCOME, "REJECTED",
                  "Webhook signature verification failed; delivery discarded.",
                  payload={"event_id": event_id, "bytes": len(raw_body or b"")})
        return Reception(status=401, outcome="INVALID_SIGNATURE",
                         error="signature verification failed")

    try:
        body = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return Reception(status=400, outcome=MALFORMED, error=str(exc))

    if not isinstance(body, dict):
        return Reception(status=400, outcome=MALFORMED,
                         error="webhook body is not an object")

    attribution = handle(body, event_id=event_id, simulated=simulated)
    status = 400 if attribution.outcome == MALFORMED else 200
    return Reception(status=status, outcome=attribution.outcome,
                     attribution=attribution)
