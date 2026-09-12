"""The idempotency key reaches Razorpay, and a retry asks before it re-sends.

`tests/test_executor.py` proves the key is claimed locally before the call. It
proves it against `client.calls`, the DRY_RUN transcript - which records the
key this wrapper was GIVEN, not what went on the wire. For a long time nothing
went on the wire: `order.create` was sent with no `receipt`, `payment_link.
create` with no `reference_id`, and the retry loop sat below the local claim
and could send the same order three times on a timeout.

These tests stand in a fake SDK and look at the payload.
"""

import pytest

from reclaim.executor import razorpay_client as rc
from reclaim.executor.razorpay_client import RazorpayClient, RazorpayError


class _Endpoint:
    """One Razorpay resource. Records every create; `all` answers from what
    was created, which is what a real lookup by receipt/reference_id does."""

    def __init__(self, name, fail_first=0, fail_with=None):
        self.name = name
        self.created: list[dict] = []
        self.creates_attempted = 0
        self.fail_first = fail_first
        self.fail_with = fail_with or TimeoutError("read timed out")

    def create(self, payload):
        self.creates_attempted += 1
        # The request LANDS even when the response is lost - the case that
        # double-charges.
        entity = {"id": f"{self.name}_{len(self.created) + 1}", **payload}
        self.created.append(entity)
        if self.creates_attempted <= self.fail_first:
            raise self.fail_with
        return entity

    def all(self, query):
        key = query.get("receipt") or query.get("reference_id")
        field = "receipt" if "receipt" in query else "reference_id"
        items = [e for e in self.created if e.get(field) == key]
        return {"items": items} if self.name == "order" else {"payment_links": items}


class _SDK:
    def __init__(self, **kw):
        self.order = _Endpoint("order", **kw)
        self.payment_link = _Endpoint("plink", **kw)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(rc.time, "sleep", lambda *_: None)


def test_order_payload_carries_the_key_as_receipt():
    sdk = _SDK()
    RazorpayClient(sdk=sdk).create_order(500, idempotency_key="REC_1:1:SILENT_RETRY")
    assert sdk.order.created[0]["receipt"] == "REC_1:1:SILENT_RETRY"


def test_payment_link_payload_carries_the_key_as_reference_id():
    sdk = _SDK()
    RazorpayClient(sdk=sdk).create_payment_link(500, idempotency_key="REC_1:1:SEND_LINK")
    assert sdk.payment_link.created[0]["reference_id"] == "REC_1:1:SEND_LINK"


def test_a_timeout_after_the_request_landed_does_not_create_a_second_order():
    """THE case. The first create succeeds at Razorpay; the response is lost.
    The old loop re-sent and Razorpay had nothing to dedupe on."""
    sdk = _SDK(fail_first=1)
    result = RazorpayClient(sdk=sdk).create_order(500, idempotency_key="REC_2:1:SILENT_RETRY")

    assert sdk.order.creates_attempted == 1, "re-sent instead of looking up"
    assert len(sdk.order.created) == 1
    assert result["id"] == "order_1", "the retry returned the original, not a copy"


def test_a_timeout_on_a_link_returns_the_original_link():
    sdk = _SDK(fail_first=1)
    result = RazorpayClient(sdk=sdk).create_payment_link(500, idempotency_key="REC_3:1:SEND_LINK")
    assert len(sdk.payment_link.created) == 1
    assert result["reference_id"] == "REC_3:1:SEND_LINK"


def test_razorpay_refusing_a_duplicate_reference_id_returns_the_existing_link():
    """The remote guard firing is a success path, not an error."""
    sdk = _SDK()
    client = RazorpayClient(sdk=sdk)
    first = client.create_payment_link(500, idempotency_key="REC_4:1:SEND_LINK")

    # Simulate Razorpay's server-side uniqueness on the second create.
    def refuse(payload):
        raise Exception("BAD_REQUEST_ERROR: reference_id already exists")
    sdk.payment_link.create = refuse

    again = client.create_payment_link(500, idempotency_key="REC_4:1:SEND_LINK")
    assert again["id"] == first["id"]
    assert len(sdk.payment_link.created) == 1


def test_a_genuine_failure_with_nothing_landed_still_retries_and_then_raises():
    """Lookup finds nothing, so the retry is the right call - and after the
    budget it raises rather than pretending."""
    class _Down(_Endpoint):
        def create(self, payload):
            self.creates_attempted += 1
            raise TimeoutError("connect timed out")  # never lands

    sdk = _SDK()
    sdk.order = _Down("order")
    with pytest.raises(RazorpayError):
        RazorpayClient(sdk=sdk).create_order(500, idempotency_key="REC_5:1:SILENT_RETRY")
    assert sdk.order.creates_attempted == rc._MAX_ATTEMPTS


def test_when_it_cannot_tell_whether_the_first_attempt_landed_it_stops():
    """The lookup itself errors. Now nobody knows if the first order exists.
    Re-sending could double-charge; stopping leaves one order, or none, and a
    record that parks for a human. A payments system takes that trade."""
    sdk = _SDK(fail_first=1)
    sdk.order.all = lambda q: (_ for _ in ()).throw(ConnectionError("lookup down"))
    with pytest.raises(RazorpayError, match="refusing to re-send"):
        RazorpayClient(sdk=sdk).create_order(500, idempotency_key="REC_6:1:SILENT_RETRY")
    assert sdk.order.creates_attempted == 1, "re-sent without knowing"
    assert len(sdk.order.created) == 1


def test_a_key_over_razorpays_forty_char_cap_is_refused_not_truncated():
    sdk = _SDK()
    with pytest.raises(RazorpayError, match="exceeds"):
        RazorpayClient(sdk=sdk).create_order(500, idempotency_key="X" * 41)
    assert sdk.order.creates_attempted == 0


def test_every_shipped_key_fits_the_cap():
    """The longest key the batch produces is well under 40; assert it stays so."""
    from reclaim.enums import ActionType
    from reclaim.models import ProposedAction
    from reclaim.timeutil import now

    longest = max(len(a.value) for a in ActionType)
    key = ProposedAction(
        record_id="USR_99999999", action_type=max(ActionType, key=lambda a: len(a.value)),
        channel=None, scheduled_for=now(), attempt_number=3,
        policy_ref="X.Y", rationale="t", amount=1,
    ).idempotency_key
    assert len(key) <= rc._MAX_KEY_LENGTH, key
    assert longest < rc._MAX_KEY_LENGTH


def test_dry_run_stub_also_carries_the_key():
    """So the DRY_RUN transcript and the live payload make the same claim."""
    client = RazorpayClient(dry_run=True)
    order = client.create_order(500, idempotency_key="REC_7:1:SILENT_RETRY")
    link = client.create_payment_link(500, idempotency_key="REC_7:1:SEND_LINK")
    assert order["receipt"] == "REC_7:1:SILENT_RETRY"
    assert link["reference_id"] == "REC_7:1:SEND_LINK"
