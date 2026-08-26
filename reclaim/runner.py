"""Batch orchestrator.

    detect -> diagnose -> decide -> guardrail -> execute -> persist -> audit

Every stage writes to the audit log, including — especially — the stage that
refuses. A blocked action is logged as loudly as an executed one, because the
blocks are what the demo turns on.

`crash_at` deliberately kills the run mid-batch. Resuming afterwards is how the
idempotency guarantee gets demonstrated rather than asserted: the second run
skips everything the first one claimed, and no key executes twice.
"""

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from . import audit
from .brain import gate
from .brain.diagnosis.engine import diagnose_batch
from .brain.policy import decide
from .brain.policy.engine import prefill_method
from . import clock
from .config import settings
from .db import AtRiskRecordRow, HumanQueueRow, SessionLocal, init_db
from .enums import ActionType, RecordState, Stage
from .executor.actions import execute, executed_keys
from .executor.channels import ChannelSender
from .executor.razorpay_client import RazorpayClient
from .repository import (
    last_attempt_at, load_records, save_batch, set_next_action_at,
)
from .settlement import SettlementResult, settle as settle_batch
from .clock import now
from .synthetic import generate

log = logging.getLogger(__name__)


# States where the agent is still the owner. Everything else belongs to a
# human, to the customer, or to nobody.
_AGENT_OWNED = frozenset({RecordState.AT_RISK, RecordState.IN_PROGRESS})
_OWNED_VALUES = frozenset(s.value for s in _AGENT_OWNED)


class BatchCrashed(RuntimeError):
    """Raised by the crash simulation. Not a real failure mode — a rehearsal
    for one."""


@dataclass
class BatchResult:
    proposed: int = 0
    scheduled: int = 0
    allowed: int = 0
    blocked: int = 0
    executed: int = 0
    skipped_idempotent: int = 0
    failed: int = 0
    escalated: int = 0
    closed: int = 0
    messages_sent: int = 0
    blocked_by: Counter = field(default_factory=Counter)
    crashed_after: int | None = None
    settlement: SettlementResult | None = None

    def as_dict(self) -> dict:
        return {
            "proposed": self.proposed,
            "scheduled": self.scheduled,
            "allowed": self.allowed,
            "blocked": self.blocked,
            "executed": self.executed,
            "skipped_idempotent": self.skipped_idempotent,
            "failed": self.failed,
            "escalated": self.escalated,
            "closed": self.closed,
            "messages_sent": self.messages_sent,
            "blocked_by_guardrail": dict(self.blocked_by.most_common()),
            "crashed_after": self.crashed_after,
            "settlement": self.settlement.as_dict() if self.settlement else None,
        }


def run_batch(
    *,
    seed: int | None = None,
    frm: datetime | None = None,
    llm=None,
    crash_at: int | None = None,
    reseed: bool = True,
    dry_run: bool | None = None,
    settle: bool = True,
    client=None,
) -> BatchResult:
    init_db()
    frm = frm or now()
    seed = seed if seed is not None else settings.seed
    batch = generate(seed=seed)
    if reseed:
        save_batch(batch.records, batch.customers)
        records = batch.records
    else:
        # A tick continues from what the database knows, not from a freshly
        # generated fixture. Otherwise a record recovered on the last tick comes
        # back AT_RISK on this one and the agent chases money it already has.
        # Only records the agent still owns. A record already recovered,
        # escalated to a person or written off is not re-proposed on every
        # tick — re-proposing it inflates the guardrail counters with the same
        # refusal over and over and buries the ones that happened once.
        stored = load_records(state=None)
        records = [r for r in stored if r.state in _AGENT_OWNED] or batch.records

    diagnoses, signals = diagnose_batch(records, batch.traffic, llm=llm)
    for record in records:
        d = diagnoses[record.id]
        audit.log(record.id, Stage.DIAGNOSE, d.root_cause.value, d.reasoning,
                  payload={"confidence": d.confidence, "source": d.source,
                           "evidence_used": d.evidence_used})

    anchors = last_attempt_at()
    proposals = [
        decide(r, diagnoses[r.id], frm=frm, anchor=anchors.get(r.id, r.detected_at))
        for r in records
    ]
    for action in proposals:
        audit.log(action.record_id, Stage.DECIDE, action.action_type.value,
                  action.rationale,
                  payload={"policy_ref": action.policy_ref,
                           "scheduled_for": action.scheduled_for.isoformat(),
                           "attempt": action.attempt_number})

    # A schedule the runner ignores is decoration. "It did not retry
    # immediately, it waited for the 1st" only means something if an action
    # dated the 1st actually sits still until then. Not-yet-due actions are
    # parked on the record and picked up by a later tick.
    actions, waiting = [], []
    for action in proposals:
        if action.scheduled_for > frm:
            waiting.append(action)
            continue
        # An overdue action fires NOW, so the guardrails must judge now. A
        # contact scheduled for 03:00 last Tuesday and executed this afternoon
        # is an afternoon contact; leaving the stale timestamp on it would have
        # quiet hours refuse a message nobody is being woken by. The
        # idempotency key does not depend on this field, so nothing drifts.
        actions.append(action if action.scheduled_for == frm
                       else action.model_copy(update={"scheduled_for": frm}))

    for action in waiting:
        set_next_action_at(action.record_id, action.scheduled_for)
        audit.log(action.record_id, Stage.DECIDE, "SCHEDULED",
                  f"{action.action_type.value} is not due until "
                  f"{action.scheduled_for:%Y-%m-%d %H:%M} IST.",
                  deferred_until=action.scheduled_for,
                  payload={"policy_ref": action.policy_ref,
                           "attempt": action.attempt_number})

    customers = {c.id: c for c in batch.customers}
    report = gate.run(records, diagnoses, actions, customers,
                      frm=frm, executed_keys=executed_keys())

    result = BatchResult(proposed=report.proposed, scheduled=len(waiting),
                         allowed=report.allowed, blocked=report.blocked,
                         blocked_by=report.blocked_by)

    client = client or RazorpayClient(dry_run=dry_run)
    sender = ChannelSender(dry_run=dry_run)
    by_id = {r.id: r for r in records}

    for i, outcome in enumerate(report.outcomes):
        action, verdict = outcome.action, outcome.result

        if not verdict.allowed:
            for violation in verdict.violations:
                audit.log(action.record_id, Stage.GUARDRAIL, "BLOCKED",
                          violation.reason, guardrail=violation.guardrail,
                          deferred_until=violation.deferred_until,
                          payload={"policy_ref": action.policy_ref,
                                   "action_type": action.action_type.value,
                                   "requires_human": violation.requires_human})
            if verdict.requires_human:
                _queue_for_human(action, verdict)
            else:
                # A refusal that ends the record ends it. Re-proposing an
                # opted-out customer on every tick writes the same refusal into
                # the trail a dozen times, inflates the guardrail counters with
                # one decision counted twelve ways, and makes the log unreadable
                # exactly where a judge is most likely to read it.
                closing = next(
                    (v for v in verdict.violations if v.closes_record), None)
                if closing is not None:
                    _close(action, result, reason=closing.reason)
            continue

        audit.log(action.record_id, Stage.GUARDRAIL, "ALLOWED",
                  "All guardrails passed.",
                  payload={"idempotency_key": action.idempotency_key})

        if action.action_type is ActionType.ESCALATE:
            _queue_for_human(action, verdict)
            result.escalated += 1
            continue

        if action.action_type is ActionType.NO_ACTION:
            # A stopping rule firing, not a no-op. The policy schedule ran out,
            # so the agent stops and says so. Executing it would claim an
            # idempotency key for an action that does nothing, and every later
            # tick would then re-propose the same nothing and be refused as a
            # replay - a guardrail counter full of noise instead of decisions.
            _close(action, result)
            continue

        record = by_id[action.record_id]
        execution = execute(
            action,
            customer=customers.get(record.counterparty_id),
            root_cause=diagnoses[action.record_id].root_cause,
            prefill_method=prefill_method(action.policy_ref),
            client=client, sender=sender,
        )

        if execution.skipped:
            result.skipped_idempotent += 1
            audit.log(action.record_id, Stage.EXECUTE, "SKIPPED_IDEMPOTENT",
                      f"Key {action.idempotency_key} already executed.",
                      guardrail="idempotency")
        elif execution.ok:
            result.executed += 1
            if execution.delivery and execution.delivery.ok:
                result.messages_sent += 1
            audit.log(action.record_id, Stage.EXECUTE, "EXECUTED",
                      f"{action.action_type.value} via "
                      f"{action.channel.value if action.channel else 'no channel'}.",
                      payload={"razorpay_ref": execution.razorpay_ref,
                               "link": execution.link_url,
                               "idempotency_key": action.idempotency_key})
        else:
            result.failed += 1
            audit.log(action.record_id, Stage.EXECUTE, "FAILED", execution.error or "",
                      payload={"idempotency_key": action.idempotency_key})

        if crash_at is not None and (i + 1) >= crash_at:
            result.crashed_after = i + 1
            raise BatchCrashed(
                f"Simulated crash after {i + 1} actions. The batch is resumable: "
                f"re-run and every claimed key will be skipped."
            )

    if settle:
        result.settlement = settle_batch(batch.truth, seed=seed)

    return result


def _close(action, result, reason: str | None = None) -> None:
    """The agent has done everything policy permits and stops. Recorded loudly:
    a stopping rule that leaves no trace is indistinguishable from a bug."""
    with SessionLocal() as session:
        record = session.get(AtRiskRecordRow, action.record_id)
        if record is not None and record.state in _OWNED_VALUES:
            record.state = RecordState.CLOSED.value
            record.next_action_at = None
            session.commit()
    result.closed += 1
    audit.log(action.record_id, Stage.EXECUTE, "STOPPED",
              reason or action.rationale,
              payload={"policy_ref": action.policy_ref,
                       "attempt": action.attempt_number})


def _queue_for_human(action, verdict) -> None:
    """One open row per record. Ticks re-propose the same escalation every time
    they run, and a queue that grows by 120 rows an hour is a queue nobody
    works."""
    reason = (verdict.violations[0].reason if verdict.violations
              else action.rationale)
    with SessionLocal() as session:
        existing = (session.query(HumanQueueRow)
                    .filter(HumanQueueRow.record_id == action.record_id)
                    .filter(HumanQueueRow.resolved_at.is_(None))
                    .first())
        if existing is None:
            session.add(HumanQueueRow(record_id=action.record_id, reason=reason,
                                      amount=action.amount))
        # Handing a record to a person ends the agent's claim on it. Without
        # this the agent proposes the same escalation on every tick and gets
        # refused every time, which is noise, not restraint.
        record = session.get(AtRiskRecordRow, action.record_id)
        if record is not None and record.state in _OWNED_VALUES:
            record.state = RecordState.ESCALATED.value
            record.next_action_at = None
        session.commit()


def tick(
    *,
    advance: str | None = None,
    seed: int | None = None,
    llm=None,
    dry_run: bool | None = None,
    client=None,
) -> tuple[BatchResult, datetime]:
    """Advance the demo clock and run the pipeline again over stored state.

    This is where deferred work lands. Actions parked by quiet hours, by a
    cooldown, or by their own schedule become due, fire, and settle — which is
    the only way "scheduled for the 1st" is observable inside a five-minute
    demo.
    """
    if advance:
        clock.advance(advance)
    at = clock.now()
    return run_batch(seed=seed, frm=at, llm=llm, reseed=False,
                     dry_run=dry_run, client=client), at


# The moments the policy table actually talks about. Walking these in order
# takes the batch from "nothing is due yet" to a settled scoreboard.
DEMO_ARC = ["15m", "20m", "2h", "24h", "48h", "next_salary_window", "+7d", "+7d"]
