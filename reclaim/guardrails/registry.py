"""Guardrail registry.

Order is display order only - evaluate_all runs every rule regardless, because
collecting all violations is what makes the audit trail worth reading.
"""

from reclaim.guardrails.base import Guardrail
from reclaim.guardrails.rules.confidence_floor import ConfidenceFloor
from reclaim.guardrails.rules.consent import Consent
from reclaim.guardrails.rules.cooldown import Cooldown
from reclaim.guardrails.rules.daily_budget import DailyBudget
from reclaim.guardrails.rules.dnd import DoNotDisturb
from reclaim.guardrails.rules.freshness import Freshness
from reclaim.guardrails.rules.frequency_cap import FrequencyCap
from reclaim.guardrails.rules.idempotency import Idempotency
from reclaim.guardrails.rules.kill_switch import KillSwitch
from reclaim.guardrails.rules.max_attempts import MaxAttempts
from reclaim.guardrails.rules.promise_window import PromiseWindow
from reclaim.guardrails.rules.quiet_hours import QuietHours
from reclaim.guardrails.rules.state_validity import StateValidity
from reclaim.guardrails.rules.value_ceiling import ValueCeiling

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
    PromiseWindow(),
]

GUARDRAIL_NAMES = [g.name for g in REGISTRY]
