"""FastAPI surface.

Two audiences, one app:

  * `/api/*` — everything the dashboard reads. Read-only except the three
    endpoints that drive the demo (`run-batch`, `tick`, `kill-switch`).
  * `/webhooks/razorpay` — the only endpoint the outside world posts to, and
    the only one where the request body must be read as bytes before anything
    parses it.

Every response is JSON built from the same functions the CLI prints, so a number
on the dashboard and the same number in a terminal cannot drift apart.
"""

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import desc

from .. import audit, clock
from ..config import ROOT, settings
from ..db import (
    AtRiskRecordRow,
    CustomerRow,
    HumanQueueRow,
    InterventionRow,
    SessionLocal,
    WebhookEventRow,
    init_db,
)
from ..money import format_inr
from ..webhooks import receive
from ..webhooks.signature import EVENT_ID_HEADER, SIGNATURE_HEADER

log = logging.getLogger(__name__)

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    init_db()
    if settings.seed_on_boot:
        _seed_if_empty()
    yield


def _seed_if_empty() -> None:
    """Populate a cold deployment, once.

    Guarded on the table being empty rather than on a flag alone: a restart must
    not wipe a batch somebody is presently demonstrating, and on a host with a
    persistent disk this becomes a no-op after the first boot. Failure here is
    logged and swallowed — an API that will not start because it could not
    generate demo data is worse than one serving zeroes.
    """
    from ..repository import count_records

    try:
        if count_records():
            return
        from ..runner import run_batch

        log.info("empty database at boot — seeding one batch")
        run_batch(dry_run=None)
    except Exception as exc:  # noqa: BLE001
        log.warning("boot seed failed, serving an empty scoreboard: %s", exc)


app = FastAPI(title="ReclaimAI", version="1.0", lifespan=_lifespan,
              description="AI revenue recovery agent — Razorpay Buildathon Track 03")

# The dashboard lives on Vercel and this API on Render, so every call the
# browser makes is cross-origin. CORS_ORIGINS names the production UI;
# allow_origin_regex covers Vercel's per-commit preview URLs, which change on
# every push and cannot be enumerated in advance.
#
# There are no cookies or credentials on this API, so a permissive origin list
# grants a stranger's page nothing it could not get by calling the API directly.
# The one endpoint that actually matters is /webhooks/razorpay, and that is
# guarded by an HMAC signature rather than by an origin header.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

UI_DIR = ROOT / "ui" / "out"


# --- the webhook --------------------------------------------------------------


@app.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request) -> Response:
    """Signature verification happens on `await request.body()` — the exact
    bytes Razorpay signed.

    Reading `await request.json()` and re-serialising it would change key order
    and separators, the HMAC would never match, and the usual next step is to
    stop verifying. Everything downstream of this line assumes the body is
    authentic, so this line is the whole security boundary.
    """
    raw = await request.body()
    reception = receive(
        raw,
        request.headers.get(SIGNATURE_HEADER) or request.headers.get(
            SIGNATURE_HEADER.lower()),
        event_id=request.headers.get(EVENT_ID_HEADER) or request.headers.get(
            EVENT_ID_HEADER.lower()),
    )
    return JSONResponse(reception.as_dict(), status_code=reception.status)


# --- reads --------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "dry_run": settings.dry_run,
        "autopilot_enabled": settings.autopilot_enabled,
        "razorpay_credentials": settings.has_razorpay,
        "anthropic_credentials": settings.has_anthropic,
        "model": settings.anthropic_model,
        "clock": clock.now().isoformat(),
        "time_travelled": clock.is_travelled(),
    }


@app.get("/api/scoreboard")
def scoreboard() -> dict[str, Any]:
    from ..scoreboard import compute

    return compute().as_dict()


@app.get("/api/baseline")
def baseline(seed: int = Query(default=None)) -> dict[str, Any]:
    from ..baseline import compare

    return compare(seed=seed if seed is not None else settings.seed).as_dict()


def _record_payload(row: AtRiskRecordRow, cause: str | None,
                    intervention: InterventionRow | None,
                    blocks: list[dict] | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "leak_type": row.leak_type,
        "amount_paise": row.amount,
        "amount_display": format_inr(row.amount),
        "currency": row.currency,
        "counterparty_id": row.counterparty_id,
        "source_ref": row.source_ref,
        "detected_at": row.detected_at.isoformat() if row.detected_at else None,
        "state": row.state,
        "attempts": row.attempts,
        "next_action_at": (row.next_action_at.isoformat()
                           if row.next_action_at else None),
        "root_cause": cause,
        "issuer_bank": (row.raw_signals or {}).get("issuer_bank"),
        "method": (row.raw_signals or {}).get("method"),
        "last_action": intervention.action_type if intervention else None,
        "last_action_at": (intervention.executed_at.isoformat()
                           if intervention and intervention.executed_at else None),
        "last_policy_ref": intervention.policy_ref if intervention else None,
        "last_result": intervention.result if intervention else None,
        "recovered_paise": intervention.recovered_amount if intervention else 0,
        "blocks": blocks or [],
    }


@app.get("/api/records")
def records(
    state: str | None = None,
    root_cause: str | None = None,
    blocked: bool = False,
    limit: int = Query(default=500, le=2000),
) -> dict[str, Any]:
    from ..scoreboard import diagnosed_causes

    causes = diagnosed_causes()
    blocks = _latest_blocks()

    with SessionLocal() as session:
        query = session.query(AtRiskRecordRow)
        if state:
            query = query.filter(AtRiskRecordRow.state == state.upper())
        rows = query.order_by(desc(AtRiskRecordRow.amount)).limit(limit).all()

        latest: dict[str, InterventionRow] = {}
        for row in (session.query(InterventionRow)
                    .order_by(InterventionRow.id).all()):
            latest[row.record_id] = row

        payload = [
            _record_payload(r, causes.get(r.id), latest.get(r.id),
                            blocks.get(r.id, []))
            for r in rows
        ]

    if root_cause:
        payload = [p for p in payload if p["root_cause"] == root_cause.upper()]
    if blocked:
        payload = [p for p in payload if p["blocks"]]

    return {"count": len(payload), "records": payload}


def _latest_blocks() -> dict[str, list[dict[str, Any]]]:
    """The most recent guardrail refusal per (record, guardrail). The queue
    screen shows why a record is sitting still, and "blocked" without a reason
    is the same as no information at all."""
    from ..db import AuditLogRow
    from ..enums import Stage

    out: dict[str, list[dict[str, Any]]] = {}
    with SessionLocal() as session:
        rows = (session.query(AuditLogRow)
                .filter(AuditLogRow.stage == Stage.GUARDRAIL.value)
                .filter(AuditLogRow.outcome == "BLOCKED")
                .order_by(desc(AuditLogRow.id)).limit(4000).all())
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row.record_id, row.guardrail or "")
        if key in seen:
            continue
        seen.add(key)
        out.setdefault(row.record_id, []).append({
            "guardrail": row.guardrail,
            "reason": row.reason,
            "deferred_until": (row.deferred_until.isoformat()
                               if row.deferred_until else None),
            "at": row.at.isoformat() if row.at else None,
        })
    return out


@app.get("/api/records/{record_id}")
def record_detail(record_id: str) -> dict[str, Any]:
    from ..scoreboard import diagnosed_causes

    with SessionLocal() as session:
        row = session.get(AtRiskRecordRow, record_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such record")
        customer = session.get(CustomerRow, row.counterparty_id)
        interventions = (session.query(InterventionRow)
                         .filter(InterventionRow.record_id == record_id)
                         .order_by(InterventionRow.id).all())
        payload = _record_payload(
            row, diagnosed_causes().get(record_id),
            interventions[-1] if interventions else None,
            _latest_blocks().get(record_id, []))
        payload["raw_signals"] = row.raw_signals or {}
        payload["customer"] = None if customer is None else {
            "id": customer.id,
            "opted_out": customer.opted_out,
            "on_dnd": customer.on_dnd,
            "successful_payments_lifetime": customer.successful_payments_lifetime,
        }
        payload["interventions"] = [{
            "id": i.id,
            "action_type": i.action_type,
            "channel": i.channel,
            "policy_ref": i.policy_ref,
            "attempt_number": i.attempt_number,
            "scheduled_for": i.scheduled_for.isoformat() if i.scheduled_for else None,
            "executed_at": i.executed_at.isoformat() if i.executed_at else None,
            "razorpay_ref": i.razorpay_ref,
            "outcome": i.outcome,
            "result": i.result,
            "recovered_paise": i.recovered_amount,
        } for i in interventions]
    return payload


@app.get("/api/records/{record_id}/audit")
def record_audit(record_id: str) -> dict[str, Any]:
    """Demo beat #3. detected -> diagnosed (reasoning + evidence) -> policy_ref
    -> guardrail verdict -> executed -> outcome, in order, for one record."""
    rows = audit.timeline(record_id)
    if not rows:
        raise HTTPException(status_code=404, detail="no audit trail for that record")
    return {
        "record_id": record_id,
        "events": [{
            "id": r.id,
            "stage": r.stage,
            "outcome": r.outcome,
            "guardrail": r.guardrail,
            "reason": r.reason,
            "payload": r.payload or {},
            "deferred_until": (r.deferred_until.isoformat()
                               if r.deferred_until else None),
            "at": r.at.isoformat() if r.at else None,
        } for r in rows],
    }


@app.get("/api/human-queue")
def human_queue() -> dict[str, Any]:
    from ..scoreboard import diagnosed_causes

    causes = diagnosed_causes()
    with SessionLocal() as session:
        rows = (session.query(HumanQueueRow)
                .order_by(desc(HumanQueueRow.amount)).all())
        items = [{
            "id": r.id,
            "record_id": r.record_id,
            "reason": r.reason,
            "amount_paise": r.amount,
            "amount_display": format_inr(r.amount),
            "root_cause": causes.get(r.record_id),
            "raised_at": r.raised_at.isoformat() if r.raised_at else None,
            "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        } for r in rows]
    return {"count": len(items),
            "total_paise": sum(i["amount_paise"] for i in items),
            "total_display": format_inr(sum(i["amount_paise"] for i in items)),
            "items": items}


@app.get("/api/guardrails")
def guardrails() -> dict[str, Any]:
    """Every refusal, grouped. This is the screen the demo turns on."""
    from ..brain.guardrails import GUARDRAIL_NAMES
    from ..db import AuditLogRow
    from ..enums import Stage

    with SessionLocal() as session:
        rows = (session.query(AuditLogRow)
                .filter(AuditLogRow.stage == Stage.GUARDRAIL.value)
                .filter(AuditLogRow.outcome == "BLOCKED")
                .order_by(desc(AuditLogRow.id)).limit(2000).all())
        blocks = [{
            "record_id": r.record_id,
            "guardrail": r.guardrail,
            "reason": r.reason,
            "deferred_until": (r.deferred_until.isoformat()
                               if r.deferred_until else None),
            "action_type": (r.payload or {}).get("action_type"),
            "policy_ref": (r.payload or {}).get("policy_ref"),
            "at": r.at.isoformat() if r.at else None,
        } for r in rows]

    counts: dict[str, int] = {}
    for b in blocks:
        counts[b["guardrail"]] = counts.get(b["guardrail"], 0) + 1
    return {
        "registered": GUARDRAIL_NAMES,
        "fired": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "total": len(blocks),
        "blocks": blocks[:400],
    }


@app.get("/api/webhooks")
def webhook_log(limit: int = Query(default=200, le=1000)) -> dict[str, Any]:
    with SessionLocal() as session:
        rows = (session.query(WebhookEventRow)
                .order_by(desc(WebhookEventRow.id)).limit(limit).all())
        items = [{
            "event_id": r.event_id,
            "event_type": r.event_type,
            "razorpay_ref": r.razorpay_ref,
            "record_id": r.record_id,
            "amount_paise": r.amount,
            "outcome": r.outcome,
            "simulated": r.simulated,
            "received_at": r.received_at.isoformat() if r.received_at else None,
        } for r in rows]
    return {"count": len(items), "events": items}


@app.get("/api/diagnosis")
def diagnosis_accuracy(seed: int = Query(default=None)) -> dict[str, Any]:
    """Accuracy against ground truth. The generator knows what it planted, so
    this is measured rather than asserted."""
    from ..brain.diagnosis.accuracy import cohort_counterfactual, score
    from ..brain.diagnosis.engine import diagnose_batch
    from ..synthetic import generate

    batch = generate(seed=seed if seed is not None else settings.seed)
    diagnoses, signals = diagnose_batch(batch.records, batch.traffic, llm=None)
    report = score(batch.records, diagnoses, batch.truth)
    return {"accuracy": report.as_dict(),
            "cohort_counterfactual": cohort_counterfactual(
                batch.records, signals, batch.truth),
            "layer_2_available": settings.has_anthropic}


# --- the three write endpoints the demo drives -------------------------------


@app.post("/api/run-batch")
def api_run_batch(seed: int = Query(default=None),
                  reset: bool = Query(default=True)) -> dict[str, Any]:
    from ..db import reset_database
    from ..runner import run_batch

    if reset:
        reset_database()
        clock.reset()
    result = run_batch(seed=seed if seed is not None else settings.seed,
                       llm=_llm(), dry_run=None)
    return {"clock": clock.now().isoformat(), "result": result.as_dict()}


@app.post("/api/tick")
def api_tick(advance: str = Query(default="24h")) -> dict[str, Any]:
    """Time travel. Advance the clock and let deferred work land — the only way
    a retry scheduled for the 1st is observable inside a five-minute demo."""
    from ..brain.policy.schedule import ScheduleError
    from ..runner import tick

    try:
        result, at = tick(advance=advance, llm=_llm(), dry_run=None)
    except ScheduleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"clock": at.isoformat(), "advanced": advance,
            "result": result.as_dict()}


@app.post("/api/kill-switch")
def kill_switch(enabled: bool = Query(...)) -> dict[str, Any]:
    """Guardrail #1, the panic button, at runtime. Flipping it off blocks every
    action on the next tick — including the ones already scheduled."""
    settings.autopilot_enabled = enabled
    return {"autopilot_enabled": settings.autopilot_enabled}


@app.post("/api/clock/reset")
def clock_reset() -> dict[str, Any]:
    clock.reset()
    return {"clock": clock.now().isoformat(), "time_travelled": False}


def _llm():
    """None when there is no key. The batch completes either way — that is the
    fallback chain doing its job, not a degraded mode to apologise for."""
    if not settings.has_anthropic:
        return None
    from ..brain.diagnosis.llm_diagnoser import LLMDiagnoser

    llm = LLMDiagnoser()
    return llm if llm.available else None


# --- the dashboard ------------------------------------------------------------


@app.get("/")
def index() -> Response:
    page = UI_DIR / "index.html"
    if page.exists():
        return FileResponse(page)
    return JSONResponse(
        {"message": "UI not built. Run `npm run build` in ui/, or use the API.",
         "api": ["/api/scoreboard", "/api/records", "/api/human-queue",
                 "/api/guardrails", "/api/baseline"]},
        status_code=200)


def mount_ui() -> None:
    """Serve the exported Next.js build if it exists. Called at import time so a
    built UI needs no extra flag, and a missing one is not an error."""
    if not UI_DIR.exists():
        return
    from fastapi.staticfiles import StaticFiles

    app.mount("/_next", StaticFiles(directory=UI_DIR / "_next"), name="next")
    app.mount("/static", StaticFiles(directory=UI_DIR), name="static")


try:
    mount_ui()
except Exception as exc:  # a missing UI must never stop the API from serving
    log.info("UI not mounted: %s", exc)
