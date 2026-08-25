"""Guardrail registry.

Order is display order only - evaluate_all runs every rule regardless, because
collecting all violations is what makes the audit trail worth reading.
"""

from .base import Guardrail
from .rules.confidence_floor import ConfidenceFloor
from .rules.consent import Consent
from .rules.cooldown import Cooldown
from .rules.daily_budget import DailyBudget
from .rules.dnd import DoNotDisturb
from .rules.freshness import Freshness
from .rules.frequency_cap import FrequencyCap
from .rules.idempotency import Idempotency
from .rules.kill_switch import KillSwitch
from .rules.max_attempts import MaxAttempts
from .rules.quiet_hours import QuietHours
from .rules.state_validity import StateValidity
from .rules.value_ceiling import ValueCeiling

REGISTRY: list[Guardrail] = [
    KillSwitch(),
    Consent(),
    DoNotDisturb(),
    QuietHours(),
    MaxAttempts(),
    Cooldown(),
    FrequencyCap(),
    ValueCeiling(),
    DailyBudget(),
    Idempotency(),
    StateValidity(),
    ConfidenceFloor(),
    Freshness(),
]

GUARDRAIL_NAMES = [g.name for g in REGISTRY]
