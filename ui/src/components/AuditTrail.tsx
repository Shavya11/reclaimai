"use client";

// Screen 3 — the audit trail. Demo beat #3.
//
// PROJECT.md is explicit: make it readable, not pretty. One record, every
// decision that was taken about it, in order, each with the reason recorded at
// the time rather than reconstructed afterwards.
//
// detected -> diagnosed (with reasoning and the evidence used) -> policy_ref
// -> guardrail verdict -> executed -> outcome.

import { useEffect, useState } from "react";

import {
  AuditEvent,
  RecordDetail,
  fmtTime,
  get,
} from "@/lib/api";
import { Badge, Card, Empty, STAGE_TONE, STATE_TONE } from "@/components/ui";

const STAGE_TITLE: Record<string, string> = {
  DETECT: "Detected",
  DIAGNOSE: "Diagnosed",
  DECIDE: "Decided",
  GUARDRAIL: "Guardrail",
  EXECUTE: "Executed",
  OUTCOME: "Outcome",
};

export default function AuditTrail({
  recordId,
  onBack,
}: {
  recordId: string;
  onBack: () => void;
}) {
  const [detail, setDetail] = useState<RecordDetail | null>(null);
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDetail(null);
    setEvents(null);
    setError(null);
    Promise.all([
      get<RecordDetail>(`/api/records/${recordId}`),
      get<{ events: AuditEvent[] }>(`/api/records/${recordId}/audit`),
    ])
      .then(([d, a]) => {
        setDetail(d);
        setEvents(a.events);
      })
      .catch((e) => setError(String(e)));
  }, [recordId]);

  if (error) return <Card title="Audit trail"><Empty>{error}</Empty></Card>;
  if (!detail || !events)
    return <Card title="Audit trail"><Empty>Loading…</Empty></Card>;

  const signals = detail.raw_signals as Record<string, unknown>;
  const err = (signals.error ?? null) as Record<string, unknown> | null;

  return (
    <div className="space-y-4">
      <Card
        title={`Record ${detail.id}`}
        hint={`${detail.leak_type} · ${detail.counterparty_id} · detected ${fmtTime(detail.detected_at)}`}
        right={
          <button
            onClick={onBack}
            className="rounded border border-line bg-panel2 px-2 py-1 text-xs text-muted hover:text-ink"
          >
            ← back to queue
          </button>
        }
      >
        <div className="grid gap-4 md:grid-cols-4">
          <Field label="Amount" value={detail.amount_display} big />
          <Field label="Root cause" value={detail.root_cause ?? "—"} />
          <div>
            <Label>State</Label>
            <div className="mt-1">
              <Badge tone={STATE_TONE[detail.state] ?? "plain"}>
                {detail.state}
              </Badge>
            </div>
          </div>
          <Field
            label="Attempts"
            value={`${detail.attempts}${
              detail.next_action_at ? ` · next ${fmtTime(detail.next_action_at)}` : ""
            }`}
          />
        </div>

        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <div className="rounded border border-line bg-panel2 p-3">
            <Label>Signals the diagnosis saw</Label>
            <dl className="mt-2 space-y-1 text-xs">
              <Sig k="issuer" v={String(signals.issuer_bank ?? "—")} />
              <Sig k="method" v={String(signals.method ?? "—")} />
              {err && (
                <>
                  <Sig k="error.code" v={String(err.code ?? "—")} />
                  <Sig k="error.reason" v={String(err.reason ?? "—")} />
                  <Sig
                    k="error.description"
                    v={String(err.description ?? "—")}
                  />
                </>
              )}
              {detail.customer && (
                <>
                  <Sig
                    k="prior successes"
                    v={String(detail.customer.successful_payments_lifetime)}
                  />
                  <Sig
                    k="consent"
                    v={
                      detail.customer.opted_out
                        ? "OPTED OUT"
                        : detail.customer.on_dnd
                          ? "on DND"
                          : "contactable"
                    }
                  />
                </>
              )}
            </dl>
          </div>

          <div className="rounded border border-line bg-panel2 p-3">
            <Label>Interventions</Label>
            {detail.interventions.length === 0 ? (
              <p className="mt-2 text-xs text-dim">
                None. The agent never acted on this record.
              </p>
            ) : (
              <div className="mt-2 space-y-2">
                {detail.interventions.map((i) => (
                  <div key={i.id} className="text-xs">
                    <div className="flex items-center gap-2">
                      <Badge tone="blue">{i.action_type}</Badge>
                      {i.channel && <Badge>{i.channel}</Badge>}
                      {i.result && (
                        <Badge
                          tone={i.result === "RECOVERED" ? "green" : "plain"}
                        >
                          {i.result}
                        </Badge>
                      )}
                    </div>
                    <div className="num mt-1 text-dim">
                      attempt {i.attempt_number} · {i.policy_ref} ·{" "}
                      {fmtTime(i.executed_at)}
                    </div>
                    {i.razorpay_ref && (
                      <div className="num text-dim">{i.razorpay_ref}</div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </Card>

      <Card
        title="Decision trail"
        hint="Append-only. Every row was written at the moment the decision was taken, including the ones that refused."
      >
        <ol className="relative space-y-0">
          {events.map((e, idx) => (
            <li key={e.id} className="relative flex gap-3 pb-4 last:pb-0">
              <div className="flex flex-col items-center">
                <span
                  className={`mt-1 h-2 w-2 shrink-0 rounded-full ${
                    e.outcome === "BLOCKED" || e.outcome === "REJECTED"
                      ? "bg-red"
                      : e.stage === "OUTCOME" && e.outcome === "RECOVERED"
                        ? "bg-green"
                        : "bg-line"
                  }`}
                />
                {idx < events.length - 1 && (
                  <span className="mt-1 w-px flex-1 bg-line" />
                )}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={STAGE_TONE[e.stage] ?? "plain"}>
                    {STAGE_TITLE[e.stage] ?? e.stage}
                  </Badge>
                  <span
                    className={`text-xs font-medium ${
                      e.outcome === "BLOCKED" ? "text-red" : "text-ink"
                    }`}
                  >
                    {e.outcome}
                  </span>
                  {e.guardrail && <Badge tone="red">{e.guardrail}</Badge>}
                  <span className="num ml-auto text-[11px] text-dim">
                    {fmtTime(e.at)}
                  </span>
                </div>
                <p className="mt-1 text-sm text-muted">{e.reason}</p>
                <Payload payload={e.payload} deferred={e.deferred_until} />
              </div>
            </li>
          ))}
        </ol>
      </Card>
    </div>
  );
}

// Only the keys worth reading out loud on stage. The rest is noise on a
// projector.
const SHOWN = [
  "confidence",
  "source",
  "evidence_used",
  "policy_ref",
  "attempt",
  "scheduled_for",
  "idempotency_key",
  "razorpay_ref",
  "link",
  "recovered_paise",
  "event_type",
  "requires_human",
  "simulated",
];

function Payload({
  payload,
  deferred,
}: {
  payload: Record<string, unknown>;
  deferred: string | null;
}) {
  const entries = SHOWN.filter((k) => payload[k] !== undefined && payload[k] !== null)
    .map((k) => [k, payload[k]] as const);
  if (entries.length === 0 && !deferred) return null;
  return (
    <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-dim">
      {deferred && (
        <span className="text-amber">deferred until {fmtTime(deferred)}</span>
      )}
      {entries.map(([k, v]) => (
        <span key={k} className="num">
          <span className="text-dim/70">{k}=</span>
          {Array.isArray(v) ? v.join(", ") : String(v)}
        </span>
      ))}
    </div>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[11px] font-semibold uppercase tracking-widest text-dim">
      {children}
    </div>
  );
}

function Field({
  label,
  value,
  big,
}: {
  label: string;
  value: string;
  big?: boolean;
}) {
  return (
    <div>
      <Label>{label}</Label>
      <div className={`num mt-1 ${big ? "text-xl font-semibold" : "text-sm"}`}>
        {value}
      </div>
    </div>
  );
}

function Sig({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-2">
      <dt className="num shrink-0 text-dim">{k}</dt>
      <dd className="num min-w-0 flex-1 truncate text-muted" title={v}>
        {v}
      </dd>
    </div>
  );
}
