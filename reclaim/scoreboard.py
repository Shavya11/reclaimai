"""The scoreboard — PROJECT.md §9.

Computed entirely from what the database recorded: records, interventions,
attributed webhooks and the audit log. Nothing here asks the outcome simulator
what it thinks happened, and nothing is carried in memory from the run that
produced it. A number that cannot be recomputed from a cold start on the stored
rows is a number nobody should believe, including us.

Three deliberate choices about the arithmetic:

  * **Recovered means attributed.** Only money walked back from a verified
    webhook to the intervention that caused it counts. Payments the merchant
    would have received anyway are not our recovery.
  * **Unrecoverable is stated, not hidden.** Risk declines, revoked mandates and
    policy blocks are money we deliberately chose not to chase. Folding them
    into "still open" would flatter the recovery rate by shrinking nothing.
  * **Contacts per recovery counts every message.** Including the ones that
    recovered nothing. That ratio is the honest cost of the strategy, and it is
    the number the baseline comparison turns on.

at_risk == recovered + open + unrecoverable, always. `cli verify` asserts it.
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import desc, func

from .db import (
    AtRiskRecordRow,
    AuditLogRow,
    HumanQueueRow,
    InterventionRow,
    SessionLocal,
    init_db,
)
from .enums import NEVER_RETRY, RecordState, RootCause, Stage
from .money import format_inr, format_inr_short
from .webhooks.attribution import RESULT_RECOVERED

# Causes the policy table refuses to chase. Their money is written off on
# purpose, and saying so is more credible than quietly leaving it "open".
UNRECOVERABLE_CAUSES = frozenset(NEVER_RETRY)


@dataclass
class CauseLine:
    cause: str
    records: int = 0
    recovered_records: int = 0
    at_risk_paise: int = 0
    recovered_paise: int = 0
    contacts: int = 0

    @property
    def rate(self) -> float:
        return self.recovered_records / self.records if self.records else 0.0

    @property
    def value_rate(self) -> float:
        return self.recovered_paise / self.at_risk_paise if self.at_risk_paise else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "root_cause": self.cause,
            "records": self.records,
            "recovered_records": self.recovered_records,
            "at_risk_paise": self.at_risk_paise,
            "recovered_paise": self.recovered_paise,
            "contacts": self.contacts,
            "rate": round(self.rate, 4),
            "value_rate": round(self.value_rate, 4),
        }


@dataclass
class Scoreboard:
    records: int = 0
    at_risk_paise: int = 0
    recovered_paise: int = 0
    open_paise: int = 0
    unrecoverable_paise: int = 0
    recovered_records: int = 0
    open_records: int = 0
    unrecoverable_records: int = 0
    by_root_cause: list[CauseLine] = field(default_factory=list)
    guardrails_fired: dict[str, int] = field(default_factory=dict)
    guardrails_records: dict[str, int] = field(default_factory=dict)
    escalations: int = 0
    interventions: int = 0
    contacts: int = 0
    silent_retries: int = 0
    webhooks_attributed: int = 0
    label: str = "ReclaimAI"

    @property
    def recovery_rate(self) -> float:
        return self.recovered_paise / self.at_risk_paise if self.at_risk_paise else 0.0

    @property
    def record_recovery_rate(self) -> float:
        return self.recovered_records / self.records if self.records else 0.0

    @property
    def contacts_per_recovery(self) -> float:
        return self.contacts / self.recovered_records if self.recovered_records else 0.0

    @property
    def guardrails_total(self) -> int:
        return sum(self.guardrails_fired.values())

    @property
    def balances(self) -> bool:
        """Every rupee is in exactly one bucket. A scoreboard that does not add
        up is one where a rupee got counted twice."""
        return (self.recovered_paise + self.open_paise
                + self.unrecoverable_paise) == self.at_risk_paise

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "records": self.records,
            "at_risk_paise": self.at_risk_paise,
            "recovered_paise": self.recovered_paise,
            "open_paise": self.open_paise,
            "unrecoverable_paise": self.unrecoverable_paise,
            "at_risk_display": format_inr(self.at_risk_paise),
            "recovered_display": format_inr(self.recovered_paise),
            "open_display": format_inr(self.open_paise),
            "unrecoverable_display": format_inr(self.unrecoverable_paise),
            "at_risk_short": format_inr_short(self.at_risk_paise),
            "recovered_short": format_inr_short(self.recovered_paise),
            "records_recovered": self.recovered_records,
            "records_open": self.open_records,
            "records_unrecoverable": self.unrecoverable_records,
            "recovery_rate": round(self.recovery_rate, 4),
            "record_recovery_rate": round(self.record_recovery_rate, 4),
            "by_root_cause": [c.as_dict() for c in self.by_root_cause],
            "guardrails_fired": self.guardrails_fired,
            "guardrails_records": self.guardrails_records,
            "guardrails_total": self.guardrails_total,
            "escalations": self.escalations,
            "interventions": self.interventions,
            "contacts": self.contacts,
            "silent_retries": self.silent_retries,
            "contacts_per_recovery": round(self.contacts_per_recovery, 2),
            "webhooks_attributed": self.webhooks_attributed,
            "balances": self.balances,
        }


def diagnosed_causes() -> dict[str, str]:
    """The last diagnosis logged for each record. Read back out of the audit log
    rather than kept in memory, so the scoreboard survives a restart and so the
    number on screen is the one the trail can justify."""
    with SessionLocal() as session:
        rows = (session.query(AuditLogRow.record_id, AuditLogRow.outcome)
                .filter(AuditLogRow.stage == Stage.DIAGNOSE.value)
                .order_by(AuditLogRow.record_id, desc(AuditLogRow.id))
                .all())
    causes: dict[str, str] = {}
    for record_id, outcome in rows:
        causes.setdefault(record_id, outcome)
    return causes


def compute(label: str = "ReclaimAI") -> Scoreboard:
    init_db()
    board = Scoreboard(label=label)
    causes = diagnosed_causes()

    with SessionLocal() as session:
        records = session.query(AtRiskRecordRow).all()

        recovered_by_record: dict[str, int] = {}
        for record_id, amount in (
            session.query(InterventionRow.record_id,
                          func.sum(InterventionRow.recovered_amount))
            .filter(InterventionRow.result == RESULT_RECOVERED)
            .group_by(InterventionRow.record_id).all()
        ):
            recovered_by_record[record_id] = int(amount or 0)

        contacts_by_record: Counter = Counter()
        for record_id, n in (
            session.query(InterventionRow.record_id, func.count("*"))
            .filter(InterventionRow.outcome == "EXECUTED")
            .filter(InterventionRow.channel.isnot(None))
            .group_by(InterventionRow.record_id).all()
        ):
            contacts_by_record[record_id] = int(n)

        board.interventions = (session.query(InterventionRow)
                               .filter(InterventionRow.outcome == "EXECUTED").count())
        board.contacts = sum(contacts_by_record.values())
        board.silent_retries = (session.query(InterventionRow)
                                .filter(InterventionRow.outcome == "EXECUTED")
                                .filter(InterventionRow.channel.is_(None)).count())
        board.escalations = session.query(HumanQueueRow).count()
        board.webhooks_attributed = (session.query(InterventionRow)
                                     .filter(InterventionRow.result.isnot(None))
                                     .count())

        # Two different counts, both honest, and quoting the wrong one is how a
        # number stops meaning what it says. `fired` is every refusal, which
        # over a dozen ticks includes the same record deferred again and again.
        # `records` is how many distinct records each guardrail actually held
        # back. The demo line "wanted N, allowed M" is about records.
        blocked = (session.query(AuditLogRow.guardrail, func.count("*"),
                                 func.count(func.distinct(AuditLogRow.record_id)))
                   .filter(AuditLogRow.stage == Stage.GUARDRAIL.value)
                   .filter(AuditLogRow.outcome == "BLOCKED")
                   .filter(AuditLogRow.guardrail.isnot(None))
                   .group_by(AuditLogRow.guardrail).all())
        board.guardrails_fired = dict(
            sorted(((g, int(n)) for g, n, _ in blocked), key=lambda kv: -kv[1]))
        board.guardrails_records = {g: int(d) for g, _, d in blocked}

    lines: dict[str, CauseLine] = {}
    for row in records:
        cause = causes.get(row.id, RootCause.UNKNOWN.value)
        line = lines.setdefault(cause, CauseLine(cause=cause))

        board.records += 1
        board.at_risk_paise += row.amount
        line.records += 1
        line.at_risk_paise += row.amount
        line.contacts += contacts_by_record.get(row.id, 0)

        recovered = recovered_by_record.get(row.id, 0)
        if row.state == RecordState.RECOVERED.value and recovered:
            board.recovered_records += 1
            board.recovered_paise += recovered
            line.recovered_records += 1
            line.recovered_paise += recovered
            # A part payment recovers part of the record. The rest is still
            # owed, so it stays open rather than vanishing into the rounding.
            board.open_paise += max(0, row.amount - recovered)
        elif (cause in UNRECOVERABLE_CAUSES
              or row.state in (RecordState.UNRECOVERABLE.value,
                               RecordState.CLOSED.value)):
            board.unrecoverable_records += 1
            board.unrecoverable_paise += row.amount
        else:
            board.open_records += 1
            board.open_paise += row.amount

    board.by_root_cause = sorted(
        lines.values(), key=lambda c: (-c.recovered_paise, -c.at_risk_paise))
    return board
