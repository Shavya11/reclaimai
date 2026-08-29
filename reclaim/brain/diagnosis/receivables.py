"""Layer 1 for overdue invoices.

The payments layer 1 is a lookup on an error string. Receivables have no error
string, which looks at first like a reason to send every invoice to the model —
and would be wrong. Some of what makes an invoice unpaid is not an inference at
all, it is a fact already recorded in the ledger:

  * a dispute flag was raised by a person, in the ERP, on purpose
  * a partial payment either arrived or it did not
  * whether an invoice is late by the buyer's OWN terms is arithmetic

Sending those to a language model would spend a call, and a quota, to be told
something the database already knows — and would do it less reliably. What is
genuinely ambiguous is the pair that looks identical from the outside: an
invoice nobody has chased and an invoice that has simply stopped moving. Those
two are layer 2's, and they are roughly half the book, which is the same split
the payments side has.

Confidence is graded honestly. A dispute flag is 1.0 because it is a record of
somebody's decision. A partial payment is 0.9 because it is strong evidence of
an intention rather than the intention itself.
"""

from ...enums import RootCause
from ...models import AtRiskRecord, Diagnosis

# How far inside a buyer's own average an invoice must sit before "their
# approval cycle is still running" is a fact rather than a guess.
CLEAR_MARGIN_DAYS = 6


def diagnose(record: AtRiskRecord) -> Diagnosis | None:
    """A Diagnosis when the ledger already answers, else None so the caller
    falls through to layer 2. Never raises."""
    try:
        return _diagnose(record)
    except Exception:  # a broken lookup must not kill the batch
        return None


def _diagnose(record: AtRiskRecord) -> Diagnosis | None:
    signals = record.raw_signals or {}

    if signals.get("dispute_flag"):
        return Diagnosis(
            root_cause=RootCause.INVOICE_DISPUTED, confidence=1.0,
            reasoning="A dispute is recorded against this invoice. Somebody "
                      "raised it deliberately; it is not an inference and not "
                      "something to chase past.",
            recoverable=True,   # a person resolves it, and then it gets paid
            evidence_used=["dispute_flag=true"],
            source="deterministic",
        )

    partial = int(signals.get("partial_paid_paise") or 0)
    if partial > 0:
        pct = partial / record.amount if record.amount else 0.0
        return Diagnosis(
            root_cause=RootCause.BUYER_CASH_CRUNCH, confidence=0.9,
            reasoning=f"{pct:.0%} of the invoice has been paid. A buyer who "
                      f"pays part of a bill is neither disputing it nor missing "
                      f"it — they are short, and the balance is a timing "
                      f"problem rather than a refusal.",
            recoverable=True,
            evidence_used=[f"partial_paid_paise={partial}",
                           f"amount_paise={record.amount}"],
            source="deterministic",
        )

    # Late by the calendar, on time by this buyer's own habit. Arithmetic, and
    # the single most useful thing to know about a B2B receivable: a 60-day
    # payer at day 50 is not delinquent, and dunning them costs a relationship
    # to collect nothing early.
    overdue = signals.get("days_overdue")
    terms = signals.get("payment_terms_days")
    average = signals.get("avg_days_to_pay")
    if overdue is not None and terms is not None and average is not None:
        age = int(overdue) + int(terms)
        # A MARGIN, not a bare comparison. An invoice eight days late from a
        # buyer who averages twelve days late is inside their cycle by
        # arithmetic and indistinguishable from a stall by any other reading —
        # so claiming it at 0.85 confidence is overclaiming, and the honest move
        # is to let layer 2 look at it. Only a clear gap is a fact.
        if age + CLEAR_MARGIN_DAYS <= int(average):
            return Diagnosis(
                root_cause=RootCause.AWAITING_APPROVAL, confidence=0.85,
                reasoning=f"The invoice is {age} days old against this buyer's "
                          f"{int(average)}-day average. Late by the due date, "
                          f"on schedule by their own behaviour — that is an "
                          f"approval cycle running, not a delinquency.",
                recoverable=True,
                evidence_used=[f"days_overdue={overdue}",
                               f"payment_terms_days={terms}",
                               f"avg_days_to_pay={average}"],
                source="deterministic",
            )

    # What is left is the genuinely ambiguous pair — an invoice nobody received
    # and an invoice that has stalled look the same from here. Layer 2's.
    return None
