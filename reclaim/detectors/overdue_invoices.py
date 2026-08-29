"""Detects invoices that fell due and were never paid.

V2's leak type, and the proof that the V1 extension points were real: this file
is the payments detector with one enum member changed. No schema migration, no
change to AtRiskRecord, no change to the guardrail engine — the record shape
stayed generic exactly so this would be a plugin rather than a rewrite.

Against live Razorpay this would page the Invoices API for status=issued past
due_by; in DRY_RUN the store is seeded by the synthetic generator. Same output
shape either way, which is why nothing downstream knows the difference.
"""

from ..enums import LeakType
from ..models import AtRiskRecord
from ..repository import load_records


class OverdueInvoiceDetector:
    leak_type = LeakType.OVERDUE_INVOICE
    name = "overdue_invoices"

    def detect(self) -> list[AtRiskRecord]:
        return load_records(leak_type=self.leak_type)
