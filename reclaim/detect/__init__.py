from reclaim.detect.abandoned_carts import AbandonedCartDetector
from reclaim.detect.base import Detector, detect_all
from reclaim.detect.failed_mandates import FailedMandateDetector
from reclaim.detect.failed_payments import FailedPaymentDetector
from reclaim.detect.overdue_invoices import OverdueInvoiceDetector

# The registry. Adding a leak type means adding one line here.
REGISTRY: list[Detector] = [
    FailedPaymentDetector(),
    AbandonedCartDetector(),
    FailedMandateDetector(),
    OverdueInvoiceDetector(),
]

__all__ = ["REGISTRY", "Detector", "detect_all", "FailedPaymentDetector",
           "AbandonedCartDetector", "FailedMandateDetector",
           "OverdueInvoiceDetector"]
