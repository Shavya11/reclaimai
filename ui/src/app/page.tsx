"use client";

// The shell: three screens, one page, and the controls the demo is driven from.
//
// One page rather than three routes, deliberately. A static export with client
// routing means no navigation on stage, no reload between beats, and nothing
// that can 404 in front of an audience.

import { useCallback, useEffect, useState } from "react";

import {
  Health,
  QueueItem,
  RecordRow,
  Scoreboard,
  fmtTime,
  get,
  post,
} from "@/lib/api";
import { Badge, Card, Empty } from "@/components/ui";
import Dashboard from "@/components/Dashboard";
import Queue from "@/components/Queue";
import AuditTrail from "@/components/AuditTrail";

type Tab = "dashboard" | "queue" | "human" | "audit";

const TICKS = ["20m", "2h", "24h", "48h", "next_salary_window", "+7d"];

export default function Page() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [board, setBoard] = useState<Scoreboard | null>(null);
  const [records, setRecords] = useState<RecordRow[]>([]);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [openRecord, setOpenRecord] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [b, r, q, h] = await Promise.all([
        get<Scoreboard>("/api/scoreboard"),
        get<{ records: RecordRow[] }>("/api/records?limit=500"),
        get<{ items: QueueItem[] }>("/api/human-queue"),
        get<Health>("/api/health"),
      ]);
      setBoard(b);
      setRecords(r.records);
      setQueue(q.items);
      setHealth(h);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const act = async (label: string, path: string) => {
    setBusy(label);
    try {
      await post(path);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const openAudit = (id: string) => {
    setOpenRecord(id);
    setTab("audit");
  };

  return (
    <main className="mx-auto max-w-[1400px] px-5 py-5">
      <header className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">
            Reclaim<span className="text-green">AI</span>
            <span className="ml-2 text-xs font-normal text-dim">
              revenue recovery agent · Razorpay Buildathon Track 03
            </span>
          </h1>
          <p className="mt-1 text-xs text-muted">
            The model produces a label. A policy table decides the action. A
            guardrail engine decides whether it may fire.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          {health && (
            <>
              <Badge tone={health.dry_run ? "amber" : "green"}>
                {health.dry_run ? "DRY_RUN" : "LIVE"}
              </Badge>
              <Badge tone={health.autopilot_enabled ? "green" : "red"}>
                autopilot {health.autopilot_enabled ? "on" : "OFF"}
              </Badge>
              <Badge tone={health.anthropic_credentials ? "green" : "plain"}>
                layer 2 {health.anthropic_credentials ? "on" : "off"}
              </Badge>
              <Badge tone={health.time_travelled ? "violet" : "plain"}>
                clock {fmtTime(health.clock)}
              </Badge>
            </>
          )}
        </div>
      </header>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <nav className="flex gap-1 rounded-lg border border-line bg-panel p-1">
          {(
            [
              ["dashboard", "Dashboard"],
              ["queue", "Recovery queue"],
              ["human", `Human queue${queue.length ? ` (${queue.length})` : ""}`],
              ["audit", "Audit trail"],
            ] as Array<[Tab, string]>
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`rounded px-3 py-1.5 text-xs font-medium transition ${
                tab === key
                  ? "bg-panel2 text-ink"
                  : "text-muted hover:text-ink"
              }`}
            >
              {label}
            </button>
          ))}
        </nav>

        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <button
            onClick={() => act("run", "/api/run-batch")}
            disabled={!!busy}
            className="rounded border border-green/40 bg-green/10 px-3 py-1.5 text-xs font-medium text-green hover:bg-green/20 disabled:opacity-50"
          >
            {busy === "run" ? "running…" : "Run batch"}
          </button>
          <span className="ml-1 text-[11px] text-dim">advance</span>
          {TICKS.map((t) => (
            <button
              key={t}
              onClick={() => act(t, `/api/tick?advance=${encodeURIComponent(t)}`)}
              disabled={!!busy}
              className="num rounded border border-line bg-panel2 px-2 py-1.5 text-[11px] text-muted hover:text-ink disabled:opacity-50"
            >
              {busy === t ? "…" : t === "next_salary_window" ? "1st" : t}
            </button>
          ))}
          <button
            onClick={() =>
              act(
                "kill",
                `/api/kill-switch?enabled=${health?.autopilot_enabled ? "false" : "true"}`,
              )
            }
            disabled={!!busy}
            title="Guardrail #1 — blocks every action while off"
            className={`rounded border px-3 py-1.5 text-xs font-medium disabled:opacity-50 ${
              health?.autopilot_enabled
                ? "border-red/40 bg-red/10 text-red hover:bg-red/20"
                : "border-green/40 bg-green/10 text-green hover:bg-green/20"
            }`}
          >
            {health?.autopilot_enabled ? "Kill switch" : "Re-arm"}
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded border border-red/30 bg-red/10 px-3 py-2 text-xs text-red">
          {error} — is the API running? <span className="num">reclaim serve</span>
        </div>
      )}

      {!board ? (
        <Card>
          <Empty>Loading…</Empty>
        </Card>
      ) : tab === "dashboard" ? (
        <Dashboard board={board} />
      ) : tab === "queue" ? (
        <Queue records={records} onOpen={openAudit} />
      ) : tab === "human" ? (
        <HumanQueue items={queue} onOpen={openAudit} />
      ) : openRecord ? (
        <AuditTrail recordId={openRecord} onBack={() => setTab("queue")} />
      ) : (
        <Card title="Audit trail">
          <Empty>Pick a record from the recovery queue.</Empty>
        </Card>
      )}

      <footer className="mt-6 text-[11px] text-dim">
        Real Razorpay APIs, real error-code shapes, real payment links.{" "}
        <strong className="text-muted">Customer response is modelled</strong> —
        per-cause success probabilities, stated openly rather than presented as
        live conversion data. Outcomes reach the scoreboard only through signed,
        verified webhooks and the attribution chain.
      </footer>
    </main>
  );
}

function HumanQueue({
  items,
  onOpen,
}: {
  items: QueueItem[];
  onOpen: (id: string) => void;
}) {
  const total = items.reduce((n, i) => n + i.amount_paise, 0);
  return (
    <Card
      title={`Human queue — ${items.length} escalations`}
      hint="Five policy rows are no_auto_action by design. Knowing when to stop and fetch a person is the differentiator, not a gap."
      right={
        <span className="num text-sm text-amber">
          {(total / 100).toLocaleString("en-IN", {
            style: "currency",
            currency: "INR",
            maximumFractionDigits: 0,
          })}
        </span>
      }
    >
      {items.length === 0 ? (
        <Empty>Nothing escalated.</Empty>
      ) : (
        <div className="-mx-4 overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm">
            <thead>
              <tr className="border-b border-line text-[11px] uppercase tracking-widest text-dim">
                <th className="px-4 py-2 text-left font-semibold">Record</th>
                <th className="px-2 py-2 text-right font-semibold">Amount</th>
                <th className="px-2 py-2 text-left font-semibold">Cause</th>
                <th className="px-4 py-2 text-left font-semibold">
                  Why a human has it
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr
                  key={i.id}
                  onClick={() => onOpen(i.record_id)}
                  className="cursor-pointer border-b border-line/60 hover:bg-panel2"
                >
                  <td className="num px-4 py-2 font-medium">{i.record_id}</td>
                  <td className="num px-2 py-2 text-right">{i.amount_display}</td>
                  <td className="px-2 py-2 text-xs">{i.root_cause ?? "—"}</td>
                  <td className="px-4 py-2 text-xs text-muted">{i.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
