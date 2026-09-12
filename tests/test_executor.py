"""Execution, messaging and the channel abstraction.

The ordering test is the important one. Claiming the idempotency key before
calling Razorpay is what makes a crash safe: the worst case becomes a claimed
key and no charge, rather than a charge nobody recorded.
"""

import pytest
from sqlalchemy import delete

from reclaim.db import ExecutedActionRow, InterventionRow, SessionLocal, init_db
from reclaim.enums import ActionType, Channel, RootCause
from reclaim.executor.actions import AlreadyExecuted, claim, execute, executed_keys
from reclaim.executor.channels import ChannelSender, recipient_for
from reclaim.executor.messages import MAX_LENGTH, fits, render
from reclaim.executor.razorpay_client import RazorpayClient, RazorpayError
from reclaim.models import ProposedAction
from reclaim.timeutil import now


@pytest.fixture(autouse=True)
def _clean_db():
    init_db()
    with SessionLocal() as s:
        s.execute(delete(ExecutedActionRow))
        s.execute(delete(InterventionRow))
        s.commit()


class _Customer:
    email = "someone@example.com"
    phone = "+919812345678"


def _action(action_type=ActionType.SEND_LINK, channel=Channel.EMAIL,
            record_id="REC_X", attempt=1, amount=12400):
    return ProposedAction(
        record_id=record_id, action_type=action_type, channel=channel,
        scheduled_for=now(), attempt_number=attempt,
        policy_ref="FAILED_PAYMENT.EXPIRED_INSTRUMENT", rationale="t", amount=amount,
    )


# --- idempotency ------------------------------------------------------------


def test_claim_succeeds_once_and_refuses_thereafter():
    action = _action()
    claim(action)
    with pytest.raises(AlreadyExecuted):
        claim(action)


def test_execute_skips_an_already_claimed_action():
    action = _action()
    claim(action)
    result = execute(action, client=RazorpayClient(dry_run=True))
    assert result.skipped is True
    assert result.ok is True


def test_key_is_claimed_before_razorpay_is_called():
    """A crash between the two must leave a claimed key and no charge, never a
    charge with no record."""
    class ExplodingClient(RazorpayClient):
        def __init__(self):
            super().__init__(dry_run=True)

        def create_payment_link(self, *a, **kw):
            # By the time Razorpay is reached, the key must already be claimed.
            assert _action().idempotency_key in executed_keys()
            raise RazorpayError("network died mid-call")

    result = execute(_action(), client=ExplodingClient())
    assert result.ok is False
    assert _action().idempotency_key in executed_keys()


def test_a_failed_call_does_not_release_the_key():
    """Releasing it would let a retry double-charge. Under-delivering on a retry
    is allowed; double-charging is not."""
    class Failing(RazorpayClient):
        def __init__(self):
            super().__init__(dry_run=True)

        def create_payment_link(self, *a, **kw):
            raise RazorpayError("502")

    execute(_action(), client=Failing())
    with pytest.raises(AlreadyExecuted):
        claim(_action())


def test_different_attempts_are_different_keys():
    claim(_action(attempt=1))
    claim(_action(attempt=2))  # must not raise
    assert len(executed_keys()) == 2


# --- execution paths --------------------------------------------------------


def test_send_link_creates_a_link_and_a_delivery():
    result = execute(_action(), customer=_Customer(),
                     root_cause=RootCause.EXPIRED_INSTRUMENT,
                     client=RazorpayClient(dry_run=True),
                     sender=ChannelSender(dry_run=True))
    assert result.ok is True
    assert result.link_url
    assert result.delivery.ok is True


def test_silent_retry_creates_no_delivery():
    """The whole point of a silent retry is that nobody is contacted."""
    result = execute(_action(ActionType.SILENT_RETRY, channel=None),
                     client=RazorpayClient(dry_run=True),
                     sender=ChannelSender(dry_run=True))
    assert result.ok is True
    assert result.delivery is None


def test_escalate_records_an_intervention_without_calling_razorpay():
    client = RazorpayClient(dry_run=True)
    result = execute(_action(ActionType.ESCALATE, channel=None), client=client)
    assert result.ok is True
    assert client.calls == []


def test_execution_is_recorded_as_an_intervention():
    execute(_action(), customer=_Customer(), root_cause=RootCause.AUTH_DROPOFF,
            client=RazorpayClient(dry_run=True))
    with SessionLocal() as s:
        rows = s.query(InterventionRow).all()
    assert len(rows) == 1
    assert rows[0].policy_ref == "FAILED_PAYMENT.EXPIRED_INSTRUMENT"


# --- messages ---------------------------------------------------------------


def test_message_carries_amount_and_link():
    text = render(RootCause.EXPIRED_INSTRUMENT, amount=12400,
                  link="https://rzp.io/i/abc", tone="neutral")
    assert "₹124" in text
    assert "https://rzp.io/i/abc" in text


def test_unknown_cause_falls_back_rather_than_crashing():
    text = render(RootCause.TECHNICAL_ERROR, amount=999, link="L")
    assert "L" in text


def test_expired_card_copy_does_not_send_them_back_to_the_card():
    text = render(RootCause.EXPIRED_INSTRUMENT, amount=1000, link="L",
                  tone="neutral")
    assert "UPI" in text


def test_sms_copy_fits_the_channel_limit():
    for cause in (RootCause.INSUFFICIENT_FUNDS, RootCause.AUTH_DROPOFF,
                  RootCause.CART_ABANDONMENT):
        text = render(cause, amount=8500000, link="https://rzp.io/i/abcdefg")
        assert fits(Channel.SMS, text), f"{cause.value} exceeds {MAX_LENGTH[Channel.SMS]}"


# --- channels ---------------------------------------------------------------


def test_channel_picks_the_right_contact_detail():
    c = _Customer()
    assert recipient_for(Channel.EMAIL, c) == c.email
    assert recipient_for(Channel.SMS, c) == c.phone


def test_missing_recipient_is_a_failed_delivery_not_a_crash():
    sender = ChannelSender(dry_run=True)
    delivery = sender.send(Channel.SMS, "", "hello")
    assert delivery.ok is False
    assert delivery.error


def test_sender_records_every_delivery():
    sender = ChannelSender(dry_run=True)
    sender.send(Channel.EMAIL, "a@b.c", "one")
    sender.send(Channel.SMS, "+919812345678", "two")
    assert len(sender.sent) == 2


def test_dry_run_makes_no_live_call():
    client = RazorpayClient(dry_run=True)
    out = client.create_payment_link(1000, idempotency_key="K:1:SEND_LINK")
    assert out["_dry_run"] is True


def test_every_message_tells_the_customer_how_to_opt_out():
    """Guardrail 2 honours STOP. That is only consent handling if the customer
    was told STOP exists - every template and the fallback must carry it."""
    from reclaim.executor.messages import OPT_OUT, TEMPLATES

    for (cause, tone) in TEMPLATES:
        text = render(cause, amount=50_000, link="https://rzp.io/i/x", tone=tone)
        assert OPT_OUT in text, f"{cause.value}/{tone} carries no opt-out"
    # the fallback path, reached by an unknown (cause, tone)
    assert OPT_OUT in render(RootCause.UNKNOWN, amount=50_000, link="https://rzp.io/i/x")


def test_every_sms_template_still_fits_with_the_opt_out_appended():
    from reclaim.executor.messages import TEMPLATES

    for (cause, tone) in TEMPLATES:
        # the longest realistic substitution: a lakh-scale amount and a real link
        text = render(cause, amount=9_99_99_900, link="https://rzp.io/i/abcdefgh",
                      tone=tone, merchant="A Fairly Long Merchant Name Pvt Ltd")
        assert fits(Channel.SMS, text), f"{cause.value}/{tone}: {len(text)} chars"


def test_a_message_over_the_channel_limit_is_refused_not_truncated():
    """A truncated SMS loses the link. Refusing is a delivery that did not
    happen - visible, and a contact not spent for nothing."""
    sender = ChannelSender(dry_run=True)
    too_long = "x" * (MAX_LENGTH[Channel.SMS] + 1)
    delivery = sender.send(Channel.SMS, "+919812345678", too_long)

    assert delivery.ok is False
    assert "allows" in (delivery.error or "")
    assert delivery.provider_ref is None, "refused, yet marked as sent"


def test_a_message_within_the_limit_is_sent():
    sender = ChannelSender(dry_run=True)
    delivery = sender.send(Channel.SMS, "+919812345678", "short and fine")
    assert delivery.ok is True
