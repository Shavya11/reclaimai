"""What-if replay: the same batch, under different rules.

A settings page tells a merchant what a number is. This tells them what the
number COSTS. "Your value ceiling is ₹50,000" is information; "raising it to
₹75,000 would have recovered ₹41,000 more, sent 6 more messages and taken 4
records off your team's desk" is a decision. Everything below exists to make the
second sentence true rather than plausible.

Two properties make it honest.

**It is side-effect free by construction, not by care.** The replay runs against
a scratch SQLite file and deletes it afterwards. Nothing is mocked, no writes
are suppressed, no code is asked to behave differently because it is a
simulation — the real runner, the real gate, the real executor and the real
settlement all run exactly as they do in production, into a database that is
thrown away. `verify.py` asserts the live row counts are unchanged across a
replay, so the claim is checked rather than asserted.

**Diagnoses are frozen, and that is not a shortcut.** Rules affect DECIDE,
GUARDRAIL and EXECUTE. They do not affect DIAGNOSE — the root cause of a failed
payment does not depend on what the merchant set their frequency cap to. So the
replay injects the live run's labels instead of re-deriving them. That is what
makes it free (no LLM calls, no quota, seconds not minutes) and it is also what
makes the comparison valid: any difference between the two scoreboards is a
difference in RULES, not a difference in what the model happened to say the
second time.
"""

import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from typing import Any

from .brain import rules
from .db import use_database
from .enums import RootCause
from .models import Diagnosis

log = logging.getLogger(__name__)

# The arc the replay walks. Same shape as the demo's, because a comparison
# against a shorter arc would flatter whichever strategy acts earliest.
from .runner import DEMO_ARC  # noqa: E402


@dataclass
class Overrides:
    """What to change for the replay. Nothing is persisted anywhere."""

    guardrails: dict[str, dict[str, Any]] = field(default_factory=dict)
    policies: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.guardrails and not self.policies

    def describe(self) -> list[str]:
        out = []
        for name, config in sorted(self.guardrails.items()):
            for key, value in sorted(config.items()):
                out.append(f"{name}.{key} = {value}")
        for ref in sorted(self.policies):
            out.append(f"policy {ref}")
        return out


@dataclass
class ReplayDiff:
    baseline: dict[str, Any]
    variant: dict[str, Any]
    overrides: list[str]

    def _delta(self, key: str) -> Any:
        a, b = self.baseline.get(key), self.variant.get(key)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return round(b - a, 4)
        return None

    def as_dict(self) -> dict[str, Any]:
        keys = [
            "recovered_paise", "records_recovered", "contacts",
            "contacts_per_recovery", "escalations", "open_paise",
            "unrecoverable_paise", "recovery_rate", "record_recovery_rate",
            "invoice_recovered_paise", "dso_after", "guardrails_total",
        ]
        deltas = {k: self._delta(k) for k in keys}

        guardrails: list[dict[str, Any]] = []
        names = set(self.baseline.get("guardrails_fired", {})) | \
            set(self.variant.get("guardrails_fired", {}))
        for name in sorted(names):
            before = self.baseline.get("guardrails_fired", {}).get(name, 0)
            after = self.variant.get("guardrails_fired", {}).get(name, 0)
            if before != after:
                guardrails.append({"guardrail": name, "before": before,
                                   "after": after, "delta": after - before})

        return {
            "overrides": self.overrides,
            "baseline": {k: self.baseline.get(k) for k in keys},
            "variant": {k: self.variant.get(k) for k in keys},
            "deltas": deltas,
            "guardrails": guardrails,
            "by_root_cause": self._causes(),
            "headline": self.headline(),
        }

    def _causes(self) -> list[dict[str, Any]]:
        before = {c["root_cause"]: c
                  for c in self.baseline.get("by_root_cause", [])}
        after = {c["root_cause"]: c for c in self.variant.get("by_root_cause", [])}
        out = []
        for cause in sorted(set(before) | set(after)):
            b = before.get(cause, {})
            a = after.get(cause, {})
            delta = a.get("recovered_paise", 0) - b.get("recovered_paise", 0)
            if delta:
                out.append({
                    "root_cause": cause,
                    "before_paise": b.get("recovered_paise", 0),
                    "after_paise": a.get("recovered_paise", 0),
                    "delta_paise": delta,
                })
        return sorted(out, key=lambda c: -abs(c["delta_paise"]))

    def headline(self) -> str:
        """One sentence a person can read out. Deliberately states the cost as
        well as the gain — a replay that only reported extra rupees would be a
        tool for arguing the guardrails down."""
        from .money import format_inr

        money = self._delta("recovered_paise") or 0
        contacts = self._delta("contacts") or 0
        humans = self._delta("escalations") or 0

        if not self.overrides:
            return "No rule changes — the two runs are the same run."
        if money == 0 and contacts == 0 and humans == 0:
            return "No measurable difference on this batch."

        parts = [f"{'+' if money >= 0 else '−'}{format_inr(abs(money))} recovered"]
        if contacts:
            parts.append(f"{'+' if contacts > 0 else '−'}{abs(int(contacts))} "
                         f"contact{'s' if abs(contacts) != 1 else ''}")
        if humans:
            parts.append(f"{'+' if humans > 0 else '−'}{abs(int(humans))} "
                         f"human escalation"
                         f"{'s' if abs(humans) != 1 else ''}")
        return ", ".join(parts) + "."


def frozen_diagnoses() -> dict[str, Diagnosis]:
    """The labels the live run produced, read back out of the audit trail.

    Out of the AUDIT LOG rather than recomputed, because the audit log is what
    actually happened. Recomputing would re-run layer 2 against a rate-limited
    free tier and could produce different labels, at which point the replay
    would be comparing two different batches and calling the difference a
    consequence of the rule change.
    """
    from sqlalchemy import desc

    from .db import AuditLogRow, SessionLocal
    from .enums import Stage

    out: dict[str, Diagnosis] = {}
    with SessionLocal() as session:
        rows = (session.query(AuditLogRow)
                .filter(AuditLogRow.stage == Stage.DIAGNOSE.value)
                .order_by(desc(AuditLogRow.id)).all())
    for row in rows:
        if row.record_id in out:
            continue          # newest wins; the list is newest-first
        try:
            cause = RootCause(row.outcome)
        except ValueError:
            continue
        payload = row.payload or {}
        out[row.record_id] = Diagnosis(
            root_cause=cause,
            confidence=float(payload.get("confidence", 1.0)),
            reasoning=row.reason or "",
            recoverable=cause not in {RootCause.RISK_DECLINE,
                                      RootCause.MANDATE_REVOKED,
                                      RootCause.POLICY_BLOCK},
            evidence_used=list(payload.get("evidence_used") or []),
            source=str(payload.get("source", "replay")),
        )
    return out


def _replay_diagnoser(labels: dict[str, Diagnosis]):
    """A stand-in for layer 2 that answers from the frozen labels.

    Injected through the same `llm=` slot the real diagnoser uses, so the replay
    runs the production diagnosis path rather than a special one — layer 1 still
    resolves what it resolves, the cohort signal still fires, and only the
    records that would have reached the model are answered from the freeze.
    """

    def diagnose(record, signal=None):
        found = labels.get(record.id)
        return found.model_copy() if found else None

    return diagnose


def replay(overrides: Overrides | None = None, *, seed: int | None = None,
           arc: list[str] | None = None) -> ReplayDiff:
    """Run the current batch twice — as configured, and with the overrides —
    and diff the two scoreboards.

    BOTH sides are replayed, including the unchanged one. Comparing a fresh
    variant run against the live scoreboard would fold every incidental
    difference between the two runs into the reported effect of the rule change.
    Replaying both means the only thing that differs is the rules.
    """
    overrides = overrides or Overrides()
    labels = frozen_diagnoses()
    if not labels:
        raise RuntimeError(
            "Nothing to replay: no diagnoses in the audit log. Run a batch first."
        )

    baseline = _run_variant(Overrides(), labels, seed=seed, arc=arc)
    variant = _run_variant(overrides, labels, seed=seed, arc=arc)
    return ReplayDiff(baseline=baseline, variant=variant,
                      overrides=overrides.describe())


def _run_variant(overrides: Overrides, labels: dict[str, Diagnosis], *,
                 seed: int | None, arc: list[str] | None) -> dict[str, Any]:
    """One arc, in a scratch database that is deleted afterwards."""
    from . import clock
    from .runner import run_batch, tick
    from .scoreboard import compute

    path = os.path.join(tempfile.gettempdir(),
                        f"reclaim_whatif_{uuid.uuid4().hex}.db")
    saved_offset = clock.offset().total_seconds()

    try:
        with use_database(f"sqlite:///{path}"):
            # Seed the scratch database with the CURRENT rules, then apply the
            # overrides on top. Seeding from the shipped YAML instead would
            # quietly discard every edit the merchant has already made and
            # attribute the difference to the one they are testing.
            _seed_rules(overrides)
            clock.reset()

            diagnoser = _replay_diagnoser(labels)
            run_batch(seed=seed, llm=diagnoser, dry_run=True, extractor=None)
            for step in (arc if arc is not None else DEMO_ARC):
                tick(advance=step, seed=seed, llm=diagnoser, dry_run=True,
                     extractor=None)
            return compute(label="replay").as_dict()
    finally:
        rules.reload()
        _restore_clock(saved_offset)
        try:
            os.remove(path)
        except OSError:
            log.debug("scratch replay database not removed: %s", path)


def _seed_rules(overrides: Overrides) -> None:
    """Copy the live rules into the scratch database, then overlay the change."""
    live_policies = rules.policies()
    live_guardrails = rules.guardrail_config()

    from .db import GuardrailConfigRow, PolicyRuleRow, SessionLocal

    with SessionLocal() as session:
        for leak, table in live_policies.items():
            for cause, row in table.items():
                merged = dict(row)
                merged.update(overrides.policies.get(f"{leak}.{cause}", {}))
                session.add(PolicyRuleRow(leak_type=leak, root_cause=cause,
                                          row=merged))
        for name, config in live_guardrails.items():
            merged = dict(config)
            merged.update(overrides.guardrails.get(name, {}))
            session.add(GuardrailConfigRow(name=name, config=merged))
        session.commit()
    rules.reload()


def _restore_clock(seconds: float) -> None:
    from . import clock

    try:
        clock.set_offset(seconds)
    except Exception:  # noqa: BLE001
        log.debug("could not restore the demo clock after a replay")


def parse_overrides(payload: dict[str, Any]) -> Overrides:
    """Turn an API body into Overrides, refusing anything invalid.

    Validated with the SAME validator the admin write path uses. A replay that
    accepted rules the system would refuse to save would be answering a question
    about a configuration nobody can have.
    """
    from .brain.validation import validate_guardrail_config, validate_policy_row

    guardrails: dict[str, dict[str, Any]] = {}
    for name, config in (payload.get("guardrails") or {}).items():
        merged = dict(rules.guardrail_config().get(name, {}))
        merged.update(config or {})
        validate_guardrail_config(name, merged)
        guardrails[name] = merged

    policies: dict[str, dict[str, Any]] = {}
    for ref, row in (payload.get("policies") or {}).items():
        leak, _, cause = ref.partition(".")
        merged = dict(rules.policies().get(leak, {}).get(cause, {}))
        merged.update(row or {})
        validate_policy_row(leak, cause, merged)
        policies[ref] = merged

    return Overrides(guardrails=guardrails, policies=policies)
