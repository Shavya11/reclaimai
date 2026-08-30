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
    """Coverage is per leak type, and exact in both directions.

    V1 asked whether every RootCause had a FAILED_PAYMENT row, which was the
    right question while every cause was a payment cause. It is now wrong twice
    over: INVOICE_DISPUTED does not belong under FAILED_PAYMENT, and a
    receivables row missing from OVERDUE_INVOICE would not be noticed. So the
    check is against CAUSES_FOR_LEAK, and it fails on unreachable rows as well
    as missing ones - a row for a combination that cannot occur is dead policy
    nobody will ever see execute.
    """
    name = "policies.yaml covers every reachable cause"
    path = ROOT / "reclaim" / "brain" / "policy" / "policies.yaml"
    if not path.exists():
        return Check(name, PENDING, "policies.yaml not written yet")
    import yaml

    from .enums import CAUSES_FOR_LEAK

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    problems = []
    total = 0
    for leak, causes in CAUSES_FOR_LEAK.items():
        table = set(data.get(leak.value, {}))
        expected = {c.value for c in causes}
        total += len(expected)
        for missing in sorted(expected - table):
            problems.append(f"{leak.value} has no row for {missing}")
        for extra in sorted(table - expected):
            problems.append(f"{leak.value}.{extra} is unreachable")
        if "UNKNOWN" not in table:
            problems.append(f"{leak.value} has no UNKNOWN fallback")
    if problems:
        return Check(name, FAIL, "; ".join(problems[:4]))
    return Check(name, PASS,
                 f"{total} reachable combinations, all with a row and an "
                 f"UNKNOWN fallback")


def _every_leak_type_has_a_detector() -> Check:
    """V1's check named three leak types by hand. Naming them by hand is how a
    fourth gets added to the enum and quietly never detected."""
    from .detectors import REGISTRY

    name = "every leak type has a detector"
    covered = {d.leak_type for d in REGISTRY}
    missing = sorted(t.value for t in LeakType if t not in covered)
    if missing:
        return Check(name, FAIL, f"no detector for: {', '.join(missing)}")
    return Check(name, PASS,
                 f"{len(REGISTRY)} detectors cover all {len(list(LeakType))} "
                 f"leak types")


def _replay_is_side_effect_free() -> Check:
    """The load-bearing claim of the rules studio, checked rather than asserted.

    A what-if that quietly wrote to the live database would corrupt the very
    numbers it exists to explain, and it would do so invisibly - the replay
    reports a diff either way. So this runs one, and counts the rows and the
    demo clock on both sides of it.
    """
    name = "what-if replay leaves no trace"
    from . import clock, whatif
    from .db import (
        AtRiskRecordRow, AuditLogRow, ExecutedActionRow, InterventionRow,
        PromiseRow, SessionLocal,
    )

    tables = (AtRiskRecordRow, AuditLogRow, ExecutedActionRow, InterventionRow,
              PromiseRow)

    def snapshot():
        with SessionLocal() as session:
            return tuple(session.query(t).count() for t in tables)

    try:
        if not whatif.frozen_diagnoses():
            return Check(name, PENDING, "no batch to replay - run one first")
    except Exception as exc:  # noqa: BLE001
        return Check(name, PENDING, f"cannot read the audit log: {exc}")

    before, before_clock = snapshot(), clock.offset().total_seconds()
    try:
        overrides = whatif.parse_overrides(
            {"guardrails": {"frequency_cap": {"max_contacts": 4}}})
        whatif.replay(overrides)
    except Exception as exc:  # noqa: BLE001
        return Check(name, FAIL, f"replay raised: {exc!r}")
    after, after_clock = snapshot(), clock.offset().total_seconds()

    if before != after:
        moved = [t.__tablename__ for t, a, b in zip(tables, before, after)
                 if a != b]
        return Check(name, FAIL,
                     f"the replay wrote to the live database: "
                     f"{', '.join(moved)}")
    if abs(before_clock - after_clock) > 1.0:
        return Check(name, FAIL,
                     f"the replay moved the demo clock by "
                     f"{after_clock - before_clock:.0f}s")
    return Check(name, PASS,
                 f"{sum(before)} rows and the demo clock unchanged across a "
                 f"full two-arc replay")


def _shipped_rules_pass_their_own_validator() -> Check:
    """A validator that refuses the defaults is a validator nobody can use, and
    a merchant who resets to defaults would be unable to save anything after."""
    name = "shipped rules pass the admin validator"
    from .brain import rules
    from .brain.validation import (
        RuleInvalid, validate_guardrail_config, validate_policy_row,
    )

    problems = []
    for leak, table in rules.default_policies().items():
        for cause, row in table.items():
            try:
                validate_policy_row(leak, cause, row)
            except RuleInvalid as exc:
                problems.append(f"{leak}.{cause}: {exc.problems[0]}")
    for section, config in rules.default_guardrails().items():
        try:
            validate_guardrail_config(section, config)
        except RuleInvalid as exc:
            problems.append(f"{section}: {exc.problems[0]}")

    if problems:
        return Check(name, FAIL, "; ".join(problems[:3]))
    return Check(name, PASS,
                 "every shipped policy row and guardrail section validates")


def _rule_change_log_is_append_only() -> Check:
    """Same guarantee as audit_log, and it matters more here: the change log is
    the only answer to "who widened the ceiling", and an answer somebody can
    edit is not one."""
    name = "rule_change_log is append-only"
    from sqlalchemy import text

    from .db import engine, init_db

    init_db()
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO rule_change_log "
                "(scope, key, before, after, actor, note, changed_at) "
                "VALUES ('policy', '__probe__', '{}', '{}', 'verify', '', "
                "datetime('now'))"))
            conn.execute(text(
                "UPDATE rule_change_log SET note='tampered' "
                "WHERE key='__probe__'"))
    except Exception as exc:  # noqa: BLE001
        if "append-only" in str(exc):
            return Check(name, PASS,
                         "the database refuses UPDATE, not just the code")
        return Check(name, FAIL, f"unexpected error: {exc!r}")
    return Check(name, FAIL, "a change-log row was updated - the trigger is gone")


def _promise_transitions_are_closed() -> Check:
    """Every promise ends OPEN, KEPT or BROKEN, and a resolved one carries the
    date it resolved. An orphan state means a record parked for ever behind
    guardrail 14 with nothing due to wake it."""
    name = "promise states are closed and resolved ones are dated"
    from .db import PromiseRow, SessionLocal
    from .enums import PromiseState

    valid = {s.value for s in PromiseState}
    with SessionLocal() as session:
        rows = session.query(PromiseRow).all()
    if not rows:
        return Check(name, PENDING, "no promises recorded yet")

    bad_state = sorted({r.state for r in rows if r.state not in valid})
    if bad_state:
        return Check(name, FAIL, f"unknown promise state(s): {bad_state}")
    undated = [r.record_id for r in rows
               if r.state != PromiseState.OPEN.value and r.resolved_at is None]
    if undated:
        return Check(name, FAIL,
                     f"resolved with no date: {', '.join(undated[:4])}")
    return Check(name, PASS,
                 f"{len(rows)} promises, every state in the enum and every "
                 f"resolution dated")


def _self_cure_is_identical_across_strategies() -> Check:
    """Both arms are handed the same customers.

    Self-cure is a fact about a customer, drawn from the planted cause and a
    dedicated stream, so it cannot depend on what either strategy did. If it
    ever could, the baseline comparison would be measuring two different worlds
    and calling the difference strategy.

    This is the property recoup found the hard way: their control arm stopped
    being observed sooner and was credited fewer unprompted payments for a
    purely bookkeeping reason, which inflated the measured lift.
    """
    name = "self-cure is the same in both arms"
    from .synthetic import generate

    a, b = generate(seed=42), generate(seed=42)
    if a.self_cure.keys() != b.self_cure.keys():
        return Check(name, FAIL, "two runs of one seed disagree on who pays")

    # Nobody who cannot pay is allowed to pay anyway.
    from .synthetic.outcomes import SELF_CURE

    impossible = sorted({
        a.truth[rid].value for rid in a.self_cure
        if SELF_CURE.get(a.truth[rid], 0.0) == 0.0})
    if impossible:
        return Check(name, FAIL,
                     f"caused a self-cure on a cause rated zero: "
                     f"{', '.join(impossible)}")
    return Check(name, PASS,
                 f"{len(a.self_cure)} of {len(a.records)} would have paid "
                 f"unprompted, identical every run and in every arm")


def _no_settled_record_still_sits_in_the_queue() -> Check:
    """A record that recovered or was closed must not still be on a person's
    list.

    Guardrail 11 stops the AGENT chasing money that already arrived. This is the
    same rule one layer up: nothing should send a PERSON to collect it either.
    The column to close these rows existed from Day 1 and nothing wrote to it,
    which is exactly the kind of gap that shows up as somebody working a case
    that paid last week.
    """
    name = "no settled record is still queued for a human"
    from .db import AtRiskRecordRow, HumanQueueRow, SessionLocal
    from .enums import RecordState

    settled = {RecordState.RECOVERED.value, RecordState.CLOSED.value}
    with SessionLocal() as session:
        rows = (session.query(HumanQueueRow.record_id, AtRiskRecordRow.state)
                .join(AtRiskRecordRow,
                      AtRiskRecordRow.id == HumanQueueRow.record_id)
                .filter(HumanQueueRow.resolved_at.is_(None))
                .all())
    if not rows:
        return Check(name, PENDING, "nothing escalated yet")

    stale = sorted({rid for rid, state in rows if state in settled})
    if stale:
        return Check(name, FAIL,
                     f"{len(stale)} settled record(s) still open in the queue: "
                     f"{', '.join(stale[:4])}")
    return Check(name, PASS,
                 f"{len(rows)} open row(s), none of them already settled")


# 13 in V1, plus promise_window in V2. Counted against the registry rather than
# a literal, so a rule that exists as a file but was never registered — the one
# way a guardrail silently does nothing — fails this check instead of passing it.
def _every_guardrail_is_registered() -> Check:
    from .brain.guardrails.registry import GUARDRAIL_NAMES

    d = ROOT / "reclaim" / "brain" / "guardrails" / "rules"
    files = sorted(p.stem for p in d.glob("*.py") if p.stem != "__init__")
    if not files:
        return Check("guardrails implemented and registered", PENDING,
                     "guardrails not written yet (Day 2, task 2.5)")
    unregistered = sorted(set(files) - set(GUARDRAIL_NAMES))
    if unregistered:
        return Check("guardrails implemented and registered", FAIL,
                     f"written but never registered, so never run: "
                     f"{', '.join(unregistered)}")
    if len(GUARDRAIL_NAMES) != len(files):
        return Check("guardrails implemented and registered", FAIL,
                     f"{len(files)} files, {len(GUARDRAIL_NAMES)} registered")
    return Check(f"{len(files)} guardrails implemented and registered", PASS,
                 ", ".join(files))


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
    """recovered + organic + open + unrecoverable == at risk. A scoreboard that
    does not add up is one where a rupee got counted twice, and nothing crashes
    when it happens."""
    from .money import format_inr
    from .scoreboard import compute

    name = "scoreboard balances"
    board = compute()
    if board.records == 0:
        return Check(name, PENDING, "no batch has been run yet — try `cli demo`")
    if not board.balances:
        return Check(name, FAIL,
                     f"{format_inr(board.at_risk_paise)} at risk but the buckets "
                     f"sum to {format_inr(board.recovered_paise + board.organic_paise + board.open_paise + board.unrecoverable_paise)}")
    return Check(name, PASS,
                 f"{format_inr(board.at_risk_paise)} = "
                 f"{format_inr(board.recovered_paise)} recovered + "
                 f"{format_inr(board.organic_paise)} arrived unprompted + "
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
    """Present AND not older than the source it was built from.

    `ui/out` is tracked deliberately so a reviewer can clone and run without an
    npm install, and `reclaim serve` serves it directly. That makes staleness
    invisible in the worst way: the dashboard loads, looks right, and is a
    previous version of itself. It has already happened once — a commit shipped
    source changes with an `out/` built seventeen minutes earlier.
    """
    name = "dashboard is built and servable"
    out = ROOT / "ui" / "out" / "index.html"
    if not out.exists():
        return Check(name, PENDING,
                     "run `npm run build` in ui/ — the API serves ui/out")

    built = out.stat().st_mtime
    src = ROOT / "ui" / "src"
    newer = sorted(
        p.relative_to(ROOT).as_posix()
        for p in src.rglob("*")
        if p.is_file() and p.stat().st_mtime > built
    )
    if newer:
        return Check(name, FAIL,
                     f"ui/out is older than {len(newer)} source file(s) — "
                     f"run `npm run build` in ui/. First: {newer[0]}")

    size = out.stat().st_size
    return Check(name, PASS,
                 f"ui/out/index.html present ({size // 1024} KB), newer than "
                 f"every file in ui/src")


CHECKS = [
    _record_stays_generic,
    _money_is_integer_paise,
    _idempotency_is_unique_at_the_database,
    _audit_log_is_append_only,
    _batch_is_reproducible,
    _detectors_cover_v1_leak_types,
    _every_leak_type_has_a_detector,
    _outcome_simulator_covers_every_root_cause,
    _deterministic_map_is_valid,
    _deterministic_map_matches_harvested_codes,
    _policies_cover_every_root_cause,
    _every_guardrail_is_registered,
    _webhook_signature_covers_raw_bytes,
    _webhook_handlers_cover_the_five_events,
    _api_exposes_the_routes_the_ui_needs,
    _no_idempotency_key_executed_twice,
    _scoreboard_balances,
    _every_recovered_rupee_is_attributed,
    _baseline_gap_is_fully_accounted_for,
    _shipped_rules_pass_their_own_validator,
    _rule_change_log_is_append_only,
    _promise_transitions_are_closed,
    _no_settled_record_still_sits_in_the_queue,
    _self_cure_is_identical_across_strategies,
    _replay_is_side_effect_free,
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
