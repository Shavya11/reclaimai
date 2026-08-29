"""Policy engine: RootCause -> ProposedAction.

Deterministic by construction. The model produced a label; this table decides
what that label means. No LLM call happens here and none ever should — the
moment a model picks the action, the amount or the recipient, every guarantee
downstream of this point stops meaning anything.

Output is PROPOSED. The guardrail engine decides whether it may fire.
"""

from datetime import datetime

from ...enums import ActionType, Channel, LeakType, RootCause
from ...models import AtRiskRecord, Diagnosis, ProposedAction
from ...timeutil import now
from .. import rules
from .schedule import ScheduleError, nth

# The only place a strategy string becomes an action. Adding a strategy to
# policies.yaml without adding it here fails loudly rather than silently doing
# nothing.
STRATEGY_TO_ACTION: dict[str, ActionType] = {
    "silent_retry": ActionType.SILENT_RETRY,
    "scheduled_retry": ActionType.RETRY,
    "request_new_method": ActionType.SEND_LINK,
    "friction_reduction": ActionType.SEND_LINK,
    "no_auto_action": ActionType.ESCALATE,
    # V2. The base action a dunning step takes when the ladder does not name
    # one; every step in policies.yaml names one, so this is the fallback that
    # keeps a malformed ladder harmless rather than fatal.
    "dunning_ladder": ActionType.NOTIFY,
}


class PolicyError(ValueError):
    pass


def decide(
    record: AtRiskRecord,
    diagnosis: Diagnosis,
    *,
    frm: datetime | None = None,
    anchor: datetime | None = None,
) -> ProposedAction:
    """`frm` is now. `anchor` is what the schedule counts from.

    They are different on purpose. "20m" means twenty minutes after the failure,
    not twenty minutes after whenever the batch happens to run — anchoring on
    the clock makes every schedule permanently twenty minutes away and nothing
    ever comes due. Attempt 1 counts from detection; later attempts count from
    the attempt before them, which is what a dunning ladder actually is.
    """
    frm = frm or now()
    anchor = anchor or record.detected_at or frm
    attempt = record.attempts + 1
    leak, cause = record.leak_type.value, diagnosis.root_cause.value

    row = rules.policy_for(leak, cause)
    if row is None:
        return _escalate(record, attempt, f"{leak}.{cause}",
                         "No policy row for this combination.", frm)

    policy_ref = f"{leak}.{cause}"
    strategy = row.get("strategy")
    action_type = STRATEGY_TO_ACTION.get(strategy)
    if action_type is None:
        raise PolicyError(f"unknown strategy {strategy!r} at {policy_ref}")

    if action_type is ActionType.ESCALATE:
        return _escalate(record, attempt, policy_ref, row.get("rationale", ""), frm)

    schedule = row.get("schedule") or ["immediate"]
    max_attempts = int(row.get("max_attempts", len(schedule)))

    # Exhausting the schedule or the attempt cap is a stopping rule, not an
    # error: stop acting and hand it to a person.
    if attempt > max_attempts:
        return _no_action(record, attempt, policy_ref,
                          f"Attempt {attempt} exceeds policy max of {max_attempts}.",
                          frm)
    try:
        when = nth(schedule, attempt, anchor)
    except ScheduleError as exc:
        raise PolicyError(f"{policy_ref}: {exc}") from exc
    if when is None:
        return _no_action(record, attempt, policy_ref,
                          f"Schedule of {len(schedule)} step(s) exhausted.", frm)

    # A silent strategy that also notifies would defeat its own purpose, so the
    # notify flag only ever upgrades a retry into contact, never the reverse.
    channel = None
    if row.get("notify_customer") and action_type is not ActionType.SILENT_RETRY:
        channel = Channel(row["channel"]) if row.get("channel") else None
        if action_type is ActionType.RETRY:
            action_type = ActionType.NOTIFY

    # A dunning ladder escalates in tone and in recipient as it climbs, and the
    # rung is a function of the attempt number. Reading it here keeps the
    # sequence in policies.yaml where a merchant can argue with it, rather than
    # as a branch nobody can see from outside the code.
    step = _ladder_step(row, attempt)
    if step:
        if step.get("action"):
            try:
                action_type = ActionType(str(step["action"]))
            except ValueError as exc:
                raise PolicyError(
                    f"{policy_ref}: ladder step {attempt} names unknown action "
                    f"{step['action']!r}") from exc
        if step.get("channel"):
            channel = Channel(str(step["channel"]))
        if action_type is ActionType.ESCALATE:
            return _escalate(record, attempt, policy_ref,
                             str(step.get("rationale")
                                 or row.get("rationale", "")), frm)

    return ProposedAction(
        record_id=record.id, action_type=action_type, channel=channel,
        scheduled_for=when, attempt_number=attempt, policy_ref=policy_ref,
        rationale=str(row.get("rationale", "")).strip(), amount=record.amount,
    )


def _ladder_step(row: dict, attempt: int) -> dict:
    """The rung for this attempt, or {} when the row is not a ladder.

    Out-of-range is empty rather than an error: `max_attempts` already stopped
    the run before here, and a ladder shorter than its schedule should degrade
    to the row's own settings, not crash a batch."""
    ladder = row.get("ladder") or []
    if not isinstance(ladder, list) or not (1 <= attempt <= len(ladder)):
        return {}
    step = ladder[attempt - 1]
    return step if isinstance(step, dict) else {}


def ladder_step(policy_ref: str, attempt: int) -> dict:
    """Public read for the executor: tone and cc for this rung."""
    leak, _, cause = policy_ref.partition(".")
    return _ladder_step(rules.policy_for(leak, cause) or {}, attempt)


def tone_for(policy_ref: str, attempt: int = 1) -> str:
    """Ladder tone if the rung names one, else the row's tone, else neutral."""
    leak, _, cause = policy_ref.partition(".")
    row = rules.policy_for(leak, cause) or {}
    step = _ladder_step(row, attempt)
    return str(step.get("tone") or row.get("tone") or "neutral")


def _escalate(record, attempt, policy_ref, rationale, frm) -> ProposedAction:
    return ProposedAction(
        record_id=record.id, action_type=ActionType.ESCALATE, channel=None,
        scheduled_for=frm, attempt_number=attempt, policy_ref=policy_ref,
        rationale=rationale.strip() or "Routed to a human by policy.",
        amount=record.amount,
    )


def _no_action(record, attempt, policy_ref, rationale, frm) -> ProposedAction:
    return ProposedAction(
        record_id=record.id, action_type=ActionType.NO_ACTION, channel=None,
        scheduled_for=frm, attempt_number=attempt, policy_ref=policy_ref,
        rationale=rationale, amount=record.amount,
    )


def prefill_method(policy_ref: str) -> str | None:
    """Which method the recovery link should offer. Cards that just failed do not
    deserve a second try at the same wall."""
    leak, _, cause = policy_ref.partition(".")
    row = rules.policy_for(leak, cause) or {}
    return row.get("prefill_method")
