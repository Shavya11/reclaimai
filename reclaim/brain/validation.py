"""Validation for merchant-edited rules.

The whole risk of a dynamic rule engine is here. V1's rules were a file in the
repository, so a broken one failed in review; V2 lets somebody type a schedule
token at 6pm and have the batch read it at 6:01. Between those two facts sits
this module, and if it is weak then "rules are data" stops being an architecture
and becomes a way to take the system down from a text box.

It validates by REUSING the code that will consume the value, never by
re-describing it. A schedule token is valid if `schedule.resolve` parses it. A
strategy is valid if `STRATEGY_TO_ACTION` maps it. A channel is valid if
`Channel` accepts it. A second, parallel description of what is legal is a
second thing to keep in step, and it would drift the first time somebody adds a
strategy.

Everything is refused by default: an unknown key is an error rather than
something quietly stored and never read, because a merchant who types
`max_attempt` and sees it saved has been told a lie about what will happen
tonight.
"""

from typing import Any

from ..enums import Channel, LeakType, RootCause
from ..timeutil import now


class RuleInvalid(ValueError):
    """Carries every problem, not just the first — the same reasoning as the
    guardrail engine collecting all violations."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


POLICY_KEYS = {
    "strategy", "schedule", "max_attempts", "notify_customer", "channel",
    "tone", "prefill_method", "rationale", "escalate_to", "ladder",
}
LADDER_KEYS = {"tone", "action", "channel", "cc", "rationale"}
PREFILL_METHODS = {"upi", "card", "netbanking", "wallet", "emi"}

# A cap on the cap. A merchant may tune max_attempts; they may not turn the
# system into something that contacts one person forty times, whatever they type
# — the global hard cap in the guardrail still applies, and this refuses the
# number outright so the intent is rejected rather than silently clamped.
MAX_ATTEMPTS_CEILING = 10
MAX_SCHEDULE_STEPS = 10


def validate_policy_row(leak_type: str, root_cause: str,
                        row: dict[str, Any]) -> dict[str, Any]:
    """Return the row, or raise RuleInvalid listing everything wrong with it."""
    from .policy.engine import STRATEGY_TO_ACTION
    from .policy.schedule import ScheduleError, resolve

    problems: list[str] = []

    if leak_type not in {t.value for t in LeakType}:
        problems.append(f"unknown leak type {leak_type!r}")
    if root_cause not in {c.value for c in RootCause}:
        problems.append(f"unknown root cause {root_cause!r}")
    if not isinstance(row, dict):
        raise RuleInvalid(problems + ["policy row must be an object"])

    unknown = sorted(set(row) - POLICY_KEYS)
    if unknown:
        problems.append(
            f"unknown field(s) {', '.join(unknown)} — they would be stored and "
            f"never read, which is worse than being refused")

    strategy = row.get("strategy")
    if strategy not in STRATEGY_TO_ACTION:
        problems.append(
            f"unknown strategy {strategy!r}; known: "
            f"{', '.join(sorted(STRATEGY_TO_ACTION))}")

    schedule = row.get("schedule")
    if schedule is not None:
        if not isinstance(schedule, list) or not schedule:
            problems.append("schedule must be a non-empty list of tokens")
        elif len(schedule) > MAX_SCHEDULE_STEPS:
            problems.append(
                f"schedule has {len(schedule)} steps; at most "
                f"{MAX_SCHEDULE_STEPS} — a longer ladder is harassment with a "
                f"calendar")
        else:
            frm = now()
            for token in schedule:
                try:
                    resolve(str(token), frm)
                except ScheduleError as exc:
                    problems.append(f"schedule token {token!r}: {exc}")

    attempts = row.get("max_attempts")
    if attempts is not None:
        try:
            n = int(attempts)
            if n < 1:
                problems.append("max_attempts must be at least 1")
            elif n > MAX_ATTEMPTS_CEILING:
                problems.append(
                    f"max_attempts {n} exceeds the hard ceiling of "
                    f"{MAX_ATTEMPTS_CEILING}")
        except (TypeError, ValueError):
            problems.append(f"max_attempts must be a whole number, got {attempts!r}")

    channel = row.get("channel")
    if channel is not None and channel not in {c.value for c in Channel}:
        problems.append(
            f"unknown channel {channel!r}; known: "
            f"{', '.join(c.value for c in Channel)}")

    prefill = row.get("prefill_method")
    if prefill is not None and str(prefill).lower() not in PREFILL_METHODS:
        problems.append(f"unknown prefill_method {prefill!r}")

    if row.get("notify_customer") and not row.get("channel"):
        problems.append(
            "notify_customer is set with no channel — the action would be "
            "proposed as a contact with nowhere to send it")

    problems.extend(_validate_ladder(row))

    # Not cosmetic. The audit trail quotes the rationale back as the answer to
    # "why did it do that", and a row without one produces a decision nobody
    # can explain after the fact.
    if not str(row.get("rationale", "")).strip():
        problems.append("rationale is required — the audit trail quotes it")

    if problems:
        raise RuleInvalid(problems)
    return row


def _validate_ladder(row: dict[str, Any]) -> list[str]:
    from ..enums import ActionType

    ladder = row.get("ladder")
    if ladder is None:
        return []
    if not isinstance(ladder, list) or not ladder:
        return ["ladder must be a non-empty list of steps"]

    problems: list[str] = []
    schedule = row.get("schedule") or []
    if isinstance(schedule, list) and len(ladder) > len(schedule):
        problems.append(
            f"ladder has {len(ladder)} steps but the schedule has "
            f"{len(schedule)} — the extra rungs can never be reached")

    for i, step in enumerate(ladder, start=1):
        if not isinstance(step, dict):
            problems.append(f"ladder step {i} must be an object")
            continue
        unknown = sorted(set(step) - LADDER_KEYS)
        if unknown:
            problems.append(f"ladder step {i}: unknown field(s) "
                            f"{', '.join(unknown)}")
        action = step.get("action")
        if action is not None and action not in {a.value for a in ActionType}:
            problems.append(f"ladder step {i}: unknown action {action!r}")
        channel = step.get("channel")
        if channel is not None and channel not in {c.value for c in Channel}:
            problems.append(f"ladder step {i}: unknown channel {channel!r}")
    return problems


# --- guardrail thresholds ---------------------------------------------------
#
# Bounds, not just types. A frequency cap of 500 parses perfectly and is a
# licence to spam; a confidence floor of 0 parses perfectly and switches off the
# rule tying the model's honesty to the system's behaviour. Every bound below is
# the point past which the setting stops meaning what its name says.

BOUNDS: dict[str, dict[str, tuple[float, float]]] = {
    "max_attempts": {"global_hard_cap": (1, MAX_ATTEMPTS_CEILING)},
    "cooldown": {"hours_between_contacts": (0, 24 * 30)},
    "frequency_cap": {"max_contacts": (1, 10), "window_days": (1, 90)},
    "value_ceiling": {"requires_human_above": (0, 10_000_000_00)},
    "daily_budget": {"max_auto_actions_per_day": (1, 100_000)},
    "confidence_floor": {"minimum": (0.5, 1.0)},
    "freshness": {"max_age_days": (1, 3650)},
    "promise_window": {"grace_hours": (0, 24 * 14),
                       "max_horizon_days": (1, 180)},
}


def validate_guardrail_config(name: str, config: dict[str, Any]) -> dict[str, Any]:
    from .guardrails.registry import GUARDRAIL_NAMES
    from .rules import default_guardrails

    problems: list[str] = []
    defaults = default_guardrails()

    # Config sections are named for guardrails, but not one-to-one: kill_switch
    # and quiet_hours are sections that several rules read. Checking against the
    # shipped file rather than the registry is what keeps a legitimate section
    # from being refused as an unknown rule.
    if name not in defaults and name not in GUARDRAIL_NAMES:
        raise RuleInvalid([f"unknown guardrail config section {name!r}"])
    if not isinstance(config, dict):
        raise RuleInvalid([f"{name} config must be an object"])

    known = set(defaults.get(name, {}))
    unknown = sorted(set(config) - known)
    if unknown:
        problems.append(
            f"{name}: unknown key(s) {', '.join(unknown)} — nothing reads them")

    for key, value in config.items():
        bound = BOUNDS.get(name, {}).get(key)
        if bound is not None:
            try:
                number = float(value)
            except (TypeError, ValueError):
                problems.append(f"{name}.{key} must be a number, got {value!r}")
                continue
            low, high = bound
            if not low <= number <= high:
                problems.append(
                    f"{name}.{key} = {value} is outside the permitted range "
                    f"{low}–{high}")

    problems.extend(_validate_special(name, config))

    if problems:
        raise RuleInvalid(problems)
    return config


def _validate_special(name: str, config: dict[str, Any]) -> list[str]:
    problems: list[str] = []

    if name == "quiet_hours":
        for key in ("start", "end"):
            if key in config and not _is_hhmm(config[key]):
                problems.append(
                    f"quiet_hours.{key} must be HH:MM, got {config[key]!r}")

    if name == "value_ceiling":
        per_type = config.get("by_leak_type")
        if per_type is not None:
            if not isinstance(per_type, dict):
                problems.append("value_ceiling.by_leak_type must be an object")
            else:
                valid = {t.value for t in LeakType}
                for leak, amount in per_type.items():
                    if leak not in valid:
                        problems.append(
                            f"value_ceiling.by_leak_type: unknown leak type "
                            f"{leak!r}")
                    try:
                        if int(amount) < 0:
                            problems.append(
                                f"value_ceiling.by_leak_type.{leak} must not be "
                                f"negative")
                    except (TypeError, ValueError):
                        problems.append(
                            f"value_ceiling.by_leak_type.{leak} must be paise "
                            f"as a whole number, got {amount!r}")

    if name == "kill_switch" and "enabled" in config:
        if not isinstance(config["enabled"], bool):
            problems.append("kill_switch.enabled must be true or false")

    return problems


def _is_hhmm(value: Any) -> bool:
    try:
        hour, _, minute = str(value).partition(":")
        return 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59
    except (TypeError, ValueError):
        return False
