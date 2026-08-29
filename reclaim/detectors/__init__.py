from .abandoned_carts import AbandonedCartDetector
from .base import Detector, detect_all
from .failed_mandates import FailedMandateDetector
from .failed_payments import FailedPaymentDetector
from .overdue_invoices import OverdueInvoiceDetector

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
