"""Detects payments the bank or card declined.

In DRY_RUN the at-risk store is seeded by the synthetic generator; against live
Razorpay this same method would page the API. Either way the output shape is
identical, which is what keeps the rest of the pipeline unaware of the source.
"""

from ..enums import LeakType
from ..models import AtRiskRecord
from ..repository import load_records


class FailedPaymentDetector:
    leak_type = LeakType.FAILED_PAYMENT
    name = "failed_payments"

    def detect(self) -> list[AtRiskRecord]:
        return load_records(leak_type=self.leak_type)
