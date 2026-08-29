"""Merchant-editable rules: the write path.

Three properties, and all three are the reason this is a module rather than four
lines in a route handler.

Nothing is written that has not passed `brain/validation.py`. A rule that fails
validation is refused whole — no partial application, no clamping a number into
range and saving it anyway. Silently correcting somebody's input is how a
merchant ends up believing the ceiling is ₹5,00,000 when it is ₹50,000.

Every accepted write appends to `rule_change_log`, which the database refuses to
UPDATE or DELETE. "Who changed the value ceiling, when, and what was it before"
has an answer that cannot be edited afterwards — which is the only version of
that answer worth having in a system that moves money.

And every write bumps the rule generation, so the next batch reads the new value
and no batch ever reads half of one and half of the other.
"""

import logging
from typing import Any

from .brain import rules
from .brain.validation import RuleInvalid, validate_guardrail_config, validate_policy_row
from .clock import now
from .db import GuardrailConfigRow, PolicyRuleRow, RuleChangeRow, SessionLocal, init_db

log = logging.getLogger(__name__)


def set_policy(leak_type: str, root_cause: str, row: dict[str, Any], *,
               actor: str = "admin", note: str = "") -> dict[str, Any]:
    """Validate, store, log, reload. Raises RuleInvalid and writes nothing."""
    validate_policy_row(leak_type, root_cause, row)
    init_db()
    rules.seed_from_yaml()          # no-op once seeded; makes the first edit work

    before = rules.policies().get(leak_type, {}).get(root_cause)
    with SessionLocal() as session:
        stored = (session.query(PolicyRuleRow)
                  .filter(PolicyRuleRow.leak_type == leak_type)
                  .filter(PolicyRuleRow.root_cause == root_cause)
                  .one_or_none())
        if stored is None:
            session.add(PolicyRuleRow(leak_type=leak_type, root_cause=root_cause,
                                      row=row, updated_at=now()))
        else:
            stored.row = row
            stored.updated_at = now()
        session.add(RuleChangeRow(
            scope="policy", key=f"{leak_type}.{root_cause}",
            before=before or {}, after=row, actor=actor, note=note,
            changed_at=now(),
        ))
        session.commit()

    rules.reload()
    log.info("policy %s.%s updated by %s", leak_type, root_cause, actor)
    return row


def set_guardrail(name: str, config: dict[str, Any], *, actor: str = "admin",
                  note: str = "") -> dict[str, Any]:
    validate_guardrail_config(name, config)
    init_db()
    rules.seed_from_yaml()

    before = rules.guardrail_config().get(name)
    with SessionLocal() as session:
        stored = session.get(GuardrailConfigRow, name)
        if stored is None:
            session.add(GuardrailConfigRow(name=name, config=config,
                                           updated_at=now()))
        else:
            stored.config = config
            stored.updated_at = now()
        session.add(RuleChangeRow(
            scope="guardrail", key=name, before=before or {}, after=config,
            actor=actor, note=note, changed_at=now(),
        ))
        session.commit()

    rules.reload()
    log.info("guardrail %s updated by %s", name, actor)
    return config


def reset(*, actor: str = "admin") -> int:
    """Back to the shipped defaults, and say so in the log.

    The reset itself is logged, because "the rules are different from yesterday
    and nothing says why" is exactly the situation the change log exists to
    prevent — and a reset is the largest change anybody can make.
    """
    written = rules.reset_to_defaults()
    with SessionLocal() as session:
        session.add(RuleChangeRow(
            scope="policy", key="*", before={"note": "merchant edits"},
            after={"note": "shipped defaults"}, actor=actor,
            note="Reset every rule to the shipped defaults.", changed_at=now(),
        ))
        session.commit()
    rules.reload()
    return written


def changes(limit: int = 200) -> list[dict[str, Any]]:
    init_db()
    with SessionLocal() as session:
        rows = (session.query(RuleChangeRow)
                .order_by(RuleChangeRow.id.desc()).limit(limit).all())
        return [{
            "id": r.id,
            "scope": r.scope,
            "key": r.key,
            "before": r.before,
            "after": r.after,
            "actor": r.actor,
            "note": r.note,
            "changed_at": r.changed_at.isoformat() if r.changed_at else None,
            "diff": _diff(r.before or {}, r.after or {}),
        } for r in rows]


def _diff(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """Field-level before/after. The change log stores whole rows, because a row
    is the unit that has to be valid; the UI wants the one field that moved."""
    out = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old != new:
            out.append({"field": key, "before": old, "after": new})
    return out


def snapshot() -> dict[str, Any]:
    """The whole editable surface, with each row marked as shipped or edited.

    `modified` is what lets the UI render "default → new" rather than showing an
    edited value as though it had always said that — the difference between a
    settings page and an audit surface.
    """
    init_db()
    policies = rules.policies()
    guardrails = rules.guardrail_config()
    return {
        "policies": [
            {
                "leak_type": leak,
                "root_cause": cause,
                "row": row,
                "modified": rules.is_modified("policy", f"{leak}.{cause}"),
                "default": rules.default_policies().get(leak, {}).get(cause),
            }
            for leak, table in policies.items()
            for cause, row in table.items()
        ],
        "guardrails": [
            {
                "name": name,
                "config": config,
                "modified": rules.is_modified("guardrail", name),
                "default": rules.default_guardrails().get(name),
            }
            for name, config in guardrails.items()
        ],
        "seeded": _is_seeded(),
    }


def _is_seeded() -> bool:
    try:
        with SessionLocal() as session:
            return session.query(PolicyRuleRow).count() > 0
    except Exception:  # noqa: BLE001
        return False


__all__ = ["set_policy", "set_guardrail", "reset", "changes", "snapshot",
           "RuleInvalid"]
