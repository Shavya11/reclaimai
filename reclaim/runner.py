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
from .config import settings
from .db import HumanQueueRow, SessionLocal, init_db
from .enums import ActionType, Stage
from .executor.actions import execute, executed_keys
from .executor.channels import ChannelSender
from .executor.razorpay_client import RazorpayClient
from .repository import save_batch
from .synthetic import generate
from .timeutil import now

log = logging.getLogger(__name__)


class BatchCrashed(RuntimeError):
    """Raised by the crash simulation. Not a real failure mode — a rehearsal
    for one."""


@dataclass
class BatchResult:
    proposed: int = 0
    allowed: int = 0
    blocked: int = 0
    executed: int = 0
    skipped_idempotent: int = 0
    failed: int = 0
    escalated: int = 0
    messages_sent: int = 0
    blocked_by: Counter = field(default_factory=Counter)
    crashed_after: int | None = None

    def as_dict(self) -> dict:
        return {
            "proposed": self.proposed,
            "allowed": self.allowed,
            "blocked": self.blocked,
            "executed": self.executed,
            "skipped_idempotent": self.skipped_idempotent,
            "failed": self.failed,
            "escalated": self.escalated,
            "messages_sent": self.messages_sent,
            "blocked_by_guardrail": dict(self.blocked_by.most_common()),
            "crashed_after": self.crashed_after,
        }


def run_batch(
    *,
    seed: int | None = None,
    frm: datetime | None = None,
    llm=None,
    crash_at: int | None = None,
    reseed: bool = True,
    dry_run: bool | None = None,
) -> BatchResult:
    init_db()
    frm = frm or now()
    batch = generate(seed=seed if seed is not None else settings.seed)
    if reseed:
        save_batch(batch.records, batch.customers)

    diagnoses, signals = diagnose_batch(batch.records, batch.traffic, llm=llm)
    for record in batch.records:
        d = diagnoses[record.id]
        audit.log(record.id, Stage.DIAGNOSE, d.root_cause.value, d.reasoning,
                  payload={"confidence": d.confidence, "source": d.source,
                           "evidence_used": d.evidence_used})

    actions = [decide(r, diagnoses[r.id], frm=frm) for r in batch.records]
    for action in actions:
        audit.log(action.record_id, Stage.DECIDE, action.action_type.value,
                  action.rationale,
                  payload={"policy_ref": action.policy_ref,
                           "scheduled_for": action.scheduled_for.isoformat(),
                           "attempt": action.attempt_number})

    customers = {c.id: c for c in batch.customers}
    report = gate.run(batch.records, diagnoses, actions, customers,
                      frm=frm, executed_keys=executed_keys())

    result = BatchResult(proposed=report.proposed, allowed=report.allowed,
                         blocked=report.blocked, blocked_by=report.blocked_by)

    client = RazorpayClient(dry_run=dry_run)
    sender = ChannelSender(dry_run=dry_run)
    by_id = {r.id: r for r in batch.records}

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
            continue

        audit.log(action.record_id, Stage.GUARDRAIL, "ALLOWED",
                  "All guardrails passed.",
                  payload={"idempotency_key": action.idempotency_key})

        if action.action_type is ActionType.ESCALATE:
            _queue_for_human(action, verdict)
            result.escalated += 1

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

    return result


def _queue_for_human(action, verdict) -> None:
    reason = (verdict.violations[0].reason if verdict.violations
              else action.rationale)
    with SessionLocal() as session:
        session.add(HumanQueueRow(record_id=action.record_id, reason=reason,
                                  amount=action.amount))
        session.commit()
