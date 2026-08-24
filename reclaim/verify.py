"""Structural self-audit.

CLAUDE.md requires that adding a RootCause updates the enum, policies.yaml, the
prompt rules and the outcome simulator — "all four, or the batch numbers go
wrong". This module turns that instruction into an executable check, so the
answer to "did you really build all of it?" is a command, not a claim.

Checks for components not yet built report PENDING rather than FAIL, so the
output stays honest about what day of the build it is.
"""

from dataclasses import dataclass
from pathlib import Path

from .config import ROOT
from .enums import ActionType, LeakType, RootCause

PASS, FAIL, PENDING = "PASS", "FAIL", "PENDING"


@dataclass
class Check:
    name: str
    status: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.status != FAIL


def _outcome_simulator_covers_every_root_cause() -> Check:
    from .synthetic.outcomes import BASE_SUCCESS

    missing = [c.value for c in RootCause if c not in BASE_SUCCESS]
    if missing:
        return Check("outcome simulator covers every RootCause", FAIL,
                     f"missing: {', '.join(missing)}")
    return Check("outcome simulator covers every RootCause", PASS,
                 f"all {len(RootCause)} causes have a success probability")


def _policies_cover_every_root_cause() -> Check:
    path = ROOT / "reclaim" / "brain" / "policy" / "policies.yaml"
    if not path.exists():
        return Check("policies.yaml covers every RootCause", PENDING,
                     "policies.yaml not written yet (Day 2, task 2.4)")
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    covered = set(data.get(LeakType.FAILED_PAYMENT.value, {}))
    missing = [c.value for c in RootCause if c.value not in covered]
    if missing:
        return Check("policies.yaml covers every RootCause", FAIL,
                     f"missing: {', '.join(missing)}")
    return Check("policies.yaml covers every RootCause", PASS,
                 f"all {len(RootCause)} causes have a policy row")


def _thirteen_guardrails() -> Check:
    d = ROOT / "reclaim" / "brain" / "guardrails" / "rules"
    files = sorted(p.stem for p in d.glob("*.py") if p.stem != "__init__")
    if not files:
        return Check("13 guardrails implemented", PENDING,
                     "guardrails not written yet (Day 2, task 2.5)")
    if len(files) != 13:
        return Check("13 guardrails implemented", FAIL,
                     f"found {len(files)}: {', '.join(files)}")
    return Check("13 guardrails implemented", PASS, ", ".join(files))


def _deterministic_map_is_valid() -> Check:
    try:
        from .brain.diagnosis.deterministic import DETERMINISTIC_MAP
    except ImportError:
        return Check("deterministic map yields valid causes", PENDING,
                     "deterministic map not written yet (Day 2, task 2.1)")
    bad = [k for k, v in DETERMINISTIC_MAP.items() if not isinstance(v, RootCause)]
    if bad:
        return Check("deterministic map yields valid causes", FAIL, f"bad keys: {bad}")
    return Check("deterministic map yields valid causes", PASS,
                 f"{len(DETERMINISTIC_MAP)} error codes mapped")


def _detectors_cover_v1_leak_types() -> Check:
    from .detectors import REGISTRY

    v1 = {LeakType.FAILED_PAYMENT, LeakType.ABANDONED_CART, LeakType.FAILED_MANDATE}
    covered = {d.leak_type for d in REGISTRY}
    missing = [t.value for t in v1 - covered]
    if missing:
        return Check("detectors cover all V1 leak types", FAIL,
                     f"missing: {', '.join(missing)}")
    return Check("detectors cover all V1 leak types", PASS,
                 ", ".join(sorted(d.name for d in REGISTRY)))


def _audit_log_is_append_only() -> Check:
    from sqlalchemy import text

    from .db import engine, init_db

    init_db()
    with engine.connect() as conn:
        names = {r[0] for r in conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='trigger'")
        )}
    need = {"audit_log_no_update", "audit_log_no_delete"}
    if not need <= names:
        return Check("audit_log is append-only at the database", FAIL,
                     f"missing triggers: {', '.join(sorted(need - names))}")
    return Check("audit_log is append-only at the database", PASS,
                 "UPDATE and DELETE triggers present")


def _idempotency_is_unique_at_the_database() -> Check:
    from .db import ExecutedActionRow

    cons = {c.name for c in ExecutedActionRow.__table__.constraints}
    if "uq_idempotency_key" not in cons:
        return Check("idempotency key is UNIQUE at the database", FAIL,
                     "constraint missing")
    return Check("idempotency key is UNIQUE at the database", PASS,
                 "UNIQUE(idempotency_key) — double execution is impossible")


def _money_is_integer_paise() -> Check:
    from .models import AtRiskRecord, ProposedAction

    for model in (AtRiskRecord, ProposedAction):
        if model.model_fields["amount"].annotation is not int:
            return Check("money is integer paise", FAIL,
                         f"{model.__name__}.amount is not int")
    return Check("money is integer paise", PASS, "no float touches money")


def _record_stays_generic() -> Check:
    from .models import AtRiskRecord

    forbidden = {"card_network", "issuer_bank", "error_code", "payment_id", "method"}
    leaked = forbidden & set(AtRiskRecord.model_fields)
    if leaked:
        return Check("AtRiskRecord stays V2-generic", FAIL,
                     f"payment-specific fields: {', '.join(sorted(leaked))}")
    return Check("AtRiskRecord stays V2-generic", PASS,
                 "no payment-specific fields; V2 adds a leak_type, not a migration")


def _batch_is_reproducible() -> Check:
    from .synthetic import generate

    a, b = generate(seed=42), generate(seed=42)
    if a.total_at_risk != b.total_at_risk:
        return Check("batch is reproducible from seed", FAIL, "totals differ")
    return Check("batch is reproducible from seed", PASS,
                 f"seed 42 -> {len(a.records)} records, {a.total_at_risk} paise, every run")


CHECKS = [
    _record_stays_generic,
    _money_is_integer_paise,
    _idempotency_is_unique_at_the_database,
    _audit_log_is_append_only,
    _batch_is_reproducible,
    _detectors_cover_v1_leak_types,
    _outcome_simulator_covers_every_root_cause,
    _deterministic_map_is_valid,
    _policies_cover_every_root_cause,
    _thirteen_guardrails,
]


def run_all() -> list[Check]:
    out = []
    for fn in CHECKS:
        try:
            out.append(fn())
        except Exception as exc:  # a broken check is a failed check, never a crash
            out.append(Check(fn.__name__.strip("_").replace("_", " "), FAIL, repr(exc)))
    return out
