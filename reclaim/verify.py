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


def _deterministic_map_matches_harvested_codes() -> Check:
    """A guessed error string produces a lookup that silently never matches, so
    every record falls to the LLM and the "60% resolved for free" claim quietly
    becomes 0% with no error to notice."""
    import json

    name = "deterministic map matches harvested Razorpay codes"
    fixture = ROOT / "fixtures" / "razorpay_error_codes.json"
    if not fixture.exists():
        return Check(name, PENDING,
                     "no harvested fixture yet — run `cli harvest` (PLAN 1.2)")

    from .brain.diagnosis.deterministic import AMBIGUOUS_REASONS, DETERMINISTIC_MAP

    codes = json.loads(fixture.read_text(encoding="utf-8")).get("codes", {})
    if not codes:
        return Check(name, PENDING, "fixture present but empty")

    known = set(DETERMINISTIC_MAP) | set(AMBIGUOUS_REASONS)
    unmapped = [r for r in codes if r.lower() not in known]
    if unmapped:
        return Check(name, FAIL,
                     f"harvested but unmapped: {', '.join(sorted(unmapped))}")
    return Check(name, PASS,
                 f"all {len(codes)} harvested reasons are mapped or explicitly ambiguous")


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
    """Every field, not just the total.

    Comparing totals alone passes while timestamps drift with the wall clock —
    and timestamps decide which records fall inside quiet hours and which hour
    bucket the cohort signal groups on, so the compliance numbers quietly move
    between runs while the headline stays put. A digest catches that; a sum does
    not.
    """
    import hashlib

    from .synthetic import generate

    name = "batch is reproducible from seed"

    def digest(batch) -> str:
        h = hashlib.sha256()
        for r in batch.records:
            h.update(f"{r.id}|{r.amount}|{r.counterparty_id}|{r.leak_type.value}|"
                     f"{r.detected_at.isoformat()}|{batch.truth[r.id].value}|"
                     f"{r.raw_signals.get('issuer_bank')}".encode())
        for c in batch.customers:
            h.update(f"{c.id}|{c.opted_out}|{c.on_dnd}".encode())
        return h.hexdigest()[:16]

    a, b = generate(seed=42), generate(seed=42)
    if a.total_at_risk != b.total_at_risk:
        return Check(name, FAIL, "totals differ")
    da, db = digest(a), digest(b)
    if da != db:
        return Check(name, FAIL, f"records differ between runs ({da} vs {db})")
    if generate(seed=43).total_at_risk == a.total_at_risk:
        return Check(name, FAIL, "a different seed produced the same batch")
    return Check(name, PASS,
                 f"seed 42 -> {len(a.records)} records, {a.total_at_risk} paise, "
                 f"digest {da}, amounts AND timestamps identical every run")


def _webhook_signature_covers_raw_bytes() -> Check:
    """A verifier fed a re-serialized body never matches, and the usual response
    to "signatures never match" is to stop checking them. This asserts the
    verifier reads the bytes off the wire."""
    import json

    from .webhooks.signature import sign, verify

    name = "webhook signature verifies raw bytes"
    secret = "verify_probe_secret"
    body = {"event": "payment_link.paid", "payload": {"b": 2, "a": 1}}
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    signature = sign(raw, secret)

    if not verify(raw, signature, secret):
        return Check(name, FAIL, "a correctly signed body was rejected")
    if verify(json.dumps(body, indent=2).encode("utf-8"), signature, secret):
        return Check(name, FAIL, "a re-serialized body passed verification")
    if verify(raw, signature, "") or verify(raw, None, secret):
        return Check(name, FAIL, "verification passes without a secret or signature")
    return Check(name, PASS,
                 "raw-byte HMAC; re-serialized, unsigned and unkeyed bodies all refused")


def _webhook_handlers_cover_the_five_events() -> Check:
    from .webhooks.events import HANDLED_EVENTS

    name = "webhook handles the five outcome events"
    required = {"payment.captured", "payment_link.paid", "order.paid",
                "subscription.charged", "payment.failed"}
    missing = required - set(HANDLED_EVENTS)
    if missing:
        return Check(name, FAIL, f"missing: {', '.join(sorted(missing))}")
    return Check(name, PASS, ", ".join(sorted(required)))


def _scoreboard_balances() -> Check:
    """recovered + open + unrecoverable == at risk. A scoreboard that does not
    add up is one where a rupee got counted twice, and nothing crashes when it
    happens."""
    from .money import format_inr
    from .scoreboard import compute

    name = "scoreboard balances"
    board = compute()
    if board.records == 0:
        return Check(name, PENDING, "no batch has been run yet — try `cli demo`")
    if not board.balances:
        return Check(name, FAIL,
                     f"{format_inr(board.at_risk_paise)} at risk but the buckets "
                     f"sum to {format_inr(board.recovered_paise + board.open_paise + board.unrecoverable_paise)}")
    return Check(name, PASS,
                 f"{format_inr(board.at_risk_paise)} = "
                 f"{format_inr(board.recovered_paise)} recovered + "
                 f"{format_inr(board.open_paise)} open + "
                 f"{format_inr(board.unrecoverable_paise)} written off")


def _every_recovered_rupee_is_attributed() -> Check:
    """The scoreboard may not invent money the attribution chain did not trace
    back to an intervention."""
    from .db import InterventionRow, SessionLocal
    from .money import format_inr
    from .scoreboard import compute
    from .webhooks.attribution import RESULT_RECOVERED

    name = "every recovered rupee traces to an intervention"
    board = compute()
    if board.records == 0:
        return Check(name, PENDING, "no batch has been run yet")
    with SessionLocal() as session:
        attributed = sum(
            i.recovered_amount for i in session.query(InterventionRow)
            .filter(InterventionRow.result == RESULT_RECOVERED))
    if attributed != board.recovered_paise:
        return Check(name, FAIL,
                     f"scoreboard says {format_inr(board.recovered_paise)}, "
                     f"attribution says {format_inr(attributed)}")
    return Check(name, PASS,
                 f"{format_inr(attributed)} across {board.recovered_records} "
                 f"records, each traced from a verified webhook")


def _no_idempotency_key_executed_twice() -> Check:
    from sqlalchemy import func

    from .db import ExecutedActionRow, SessionLocal

    name = "no action executed twice"
    with SessionLocal() as session:
        total = session.query(ExecutedActionRow).count()
        if total == 0:
            return Check(name, PENDING, "nothing executed yet")
        distinct = session.query(
            func.count(func.distinct(ExecutedActionRow.idempotency_key))).scalar()
    if total != distinct:
        return Check(name, FAIL, f"{total} rows but {distinct} distinct keys")
    return Check(name, PASS,
                 f"{total} executions, {distinct} distinct keys, 0 duplicates")


def _api_exposes_the_routes_the_ui_needs() -> Check:
    name = "API exposes every documented route"
    try:
        from .api.app import app
    except Exception as exc:  # noqa: BLE001
        return Check(name, FAIL, f"api will not import: {exc!r}")

    paths = {getattr(r, "path", "") for r in app.routes}
    required = {"/api/scoreboard", "/api/records", "/api/records/{record_id}/audit",
                "/api/human-queue", "/api/run-batch", "/api/tick",
                "/webhooks/razorpay"}
    missing = required - paths
    if missing:
        return Check(name, FAIL, f"missing: {', '.join(sorted(missing))}")
    return Check(name, PASS, f"{len(required)} required routes present")


def _baseline_gap_is_fully_accounted_for() -> Check:
    """Publishing a comparison we can lose is only defensible if every rupee of
    the difference has a stated reason."""
    from .baseline import gap_analysis
    from .money import format_inr
    from .scoreboard import compute

    name = "baseline gap is fully accounted for"
    if compute().records == 0:
        return Check(name, PENDING, "no batch has been run yet")
    gap = gap_analysis()
    reasons_total = sum(r["paise"] for r in gap["reasons"])
    if reasons_total != gap["total"]["paise"]:
        return Check(name, FAIL,
                     f"{format_inr(gap['total']['paise'])} unexplained vs "
                     f"{format_inr(reasons_total)} attributed to a reason")
    if not gap["total"]["records"]:
        return Check(name, PASS, "the naive strategy recovered nothing we did not")
    return Check(name, PASS,
                 f"{gap['total']['display']} the naive run collects and we do not, "
                 f"of which {format_inr(gap['deliberate_paise'])} is refused on purpose")


def _dashboard_is_built() -> Check:
    name = "dashboard is built and servable"
    out = ROOT / "ui" / "out" / "index.html"
    if not out.exists():
        return Check(name, PENDING,
                     "run `npm run build` in ui/ — the API serves ui/out")
    size = out.stat().st_size
    return Check(name, PASS, f"ui/out/index.html present ({size // 1024} KB)")


CHECKS = [
    _record_stays_generic,
    _money_is_integer_paise,
    _idempotency_is_unique_at_the_database,
    _audit_log_is_append_only,
    _batch_is_reproducible,
    _detectors_cover_v1_leak_types,
    _outcome_simulator_covers_every_root_cause,
    _deterministic_map_is_valid,
    _deterministic_map_matches_harvested_codes,
    _policies_cover_every_root_cause,
    _thirteen_guardrails,
    _webhook_signature_covers_raw_bytes,
    _webhook_handlers_cover_the_five_events,
    _api_exposes_the_routes_the_ui_needs,
    _no_idempotency_key_executed_twice,
    _scoreboard_balances,
    _every_recovered_rupee_is_attributed,
    _baseline_gap_is_fully_accounted_for,
    _dashboard_is_built,
]


def run_all() -> list[Check]:
    out = []
    for fn in CHECKS:
        try:
            out.append(fn())
        except Exception as exc:  # a broken check is a failed check, never a crash
            out.append(Check(fn.__name__.strip("_").replace("_", " "), FAIL, repr(exc)))
    return out
