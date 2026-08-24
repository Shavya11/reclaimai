"""Harvest REAL Razorpay error codes into fixtures.

PLAN.md 1.2 asks for the exact error strings test-mode returns, because Day 2's
DETERMINISTIC_MAP keys off them literally. A guessed string produces a map that
silently never matches.

Payments cannot be created from the API — they come through Checkout — so this
runs in two halves with a browser in the middle:

    cli harvest --create     mints payment links, prints what to do with each
    <pay them in a browser, choosing Failure on the simulated bank page>
    cli harvest --collect    fetches the resulting payments, writes the fixture

The fixture it writes is what error_codes.py should be re-pointed at.
"""

import json
from pathlib import Path

from .config import ROOT
from .executor import RazorpayClient

FIXTURE = ROOT / "fixtures" / "razorpay_error_codes.json"

# Each link is a scenario to reproduce by hand. Test mode shows a simulated bank
# page after the card is entered; the Failure button there is what produces a
# genuine failed payment with populated error_* fields.
SCENARIOS = [
    ("generic_decline", 19900,
     "Card 4111 1111 1111 1111, any future expiry, any CVV -> choose FAILURE"),
    ("auth_dropoff", 24900,
     "Same card -> reach the OTP/bank page, then CLOSE the tab without paying"),
    ("upi_failure", 9900,
     "Choose UPI, id 'failure@razorpay' -> forced failure"),
    ("netbanking_failure", 14900,
     "Choose Netbanking, any bank -> choose FAILURE on the simulator"),
]


def create() -> list[dict]:
    client = RazorpayClient(dry_run=False)
    out = []
    for name, amount, how in SCENARIOS:
        link = client.create_payment_link(
            amount,
            idempotency_key=f"HARVEST:{name}:1:SEND_LINK",
            description=f"ReclaimAI error-code harvest — {name}",
            notes={"harvest_scenario": name},
        )
        out.append({"scenario": name, "how": how,
                    "url": link.get("short_url"), "link_id": link.get("id")})
    return out


def _items(response: dict, *keys: str) -> list:
    """Razorpay collection endpoints are not consistent about their list key:
    payments use `items`, payment links use `payment_links`."""
    for k in (*keys, "items"):
        if isinstance(response.get(k), list):
            return response[k]
    return []


def link_status() -> list[dict]:
    """Which harvest links have actually been paid. Answers the common failure
    mode — an empty fixture because nobody clicked anything yet."""
    client = RazorpayClient(dry_run=False)._client
    links = _items(client.payment_link.all(), "payment_links")
    return [
        {"scenario": l.get("notes", {}).get("harvest_scenario", "?"),
         "status": l.get("status"), "amount_paid": l.get("amount_paid"),
         "attempted": bool(l.get("payments")), "url": l.get("short_url")}
        for l in links
    ]


def collect() -> dict:
    """Fetch every payment on the account and keep the failed ones' error fields."""
    client = RazorpayClient(dry_run=False)._client
    payments = _items(client.payment.all({"count": 100}), "payments")
    failed = [p for p in payments if p.get("status") == "failed"]

    harvested = {}
    for p in failed:
        key = p.get("error_reason") or p.get("error_code") or "unknown"
        harvested[key] = {
            "code": p.get("error_code"),
            "reason": p.get("error_reason"),
            "source": p.get("error_source"),
            "step": p.get("error_step"),
            "description": p.get("error_description"),
            "method": p.get("method"),
            "seen_on_payment": p.get("id"),
        }

    payload = {
        "harvested_from": "razorpay test mode",
        "total_payments": len(payments),
        "failed_payments": len(failed),
        "codes": harvested,
    }
    # An empty fixture would silently overwrite a good one, so only write when
    # there is something to write.
    if harvested:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        payload["written_to"] = str(FIXTURE)
    else:
        payload["links"] = link_status()
    return payload
