"use client";

// Screen 1 — the scoreboard.
//
// Four numbers at the top, because a judge should be able to read the outcome
// from across the room. Everything under them exists to make those four
// numbers believable: what was recovered per cause, what the agent refused to
// do, and what a naive strategy would have done with the same batch.

import { useEffect, useState } from "react";

import {
  Comparison,
  Scoreboard,
  get,
  pct,
} from "@/lib/api";
import { Badge, Bar, Card, Empty, Stat } from "@/components/ui";

const GUARDRAIL_BLURB: Record<string, string> = {
  kill_switch: "autopilot off",
  consent: "customer opted out",
  dnd: "on the DND registry",
  quiet_hours: "outside 09:00–20:00 IST",
  max_attempts: "attempt cap reached",
  cooldown: "24h gap not elapsed",
  frequency_cap: "2 contacts / 7 days / customer",
  value_ceiling: "above ₹50,000 — needs a human",
  daily_budget: "daily action budget",
  idempotency: "already executed",
  state_validity: "record no longer actionable",
  confidence_floor: "diagnosis below 0.6",
  freshness: "older than 90 days",
};

export default function Dashboard({ board }: { board: Scoreboard }) {
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    get<Comparison>("/api/baseline")
      .then(setComparison)
      .catch(() => setFailed(true));
  }, [board.recovered_paise]);

  const causes = board.by_root_cause.filter((c) => c.records > 0);
  // Records held back, not raw refusals. A deferral re-evaluated on twelve
  // ticks is one decision, and quoting 95 where the honest number is 17 is the
  // kind of inflation this whole project is arguing against.
  const fired = Object.entries(board.guardrails_fired).map(
    ([name, refusals]) =>
      [name, board.guardrails_records?.[name] ?? refusals, refusals] as const,
  );
  const maxFired = Math.max(1, ...fired.map(([, held]) => held));

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat
          label="Money at risk"
          value={board.at_risk_display}
          sub={`${board.records} records detected`}
        />
        <Stat
          label="Recovered"
          value={board.recovered_display}
          tone="green"
          sub={`${pct(board.recovery_rate)} by value · ${board.records_recovered} records`}
        />
        <Stat
          label="Still open"
          value={board.open_display}
          tone="amber"
          sub={`${board.records_open} records in flight`}
        />
        <Stat
          label="Written off"
          value={board.unrecoverable_display}
          tone="red"
          sub={`${board.records_unrecoverable} deliberately not chased`}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          title="Recovery by root cause"
          hint="Diagnosis is the product. Each cause gets a different response, and the rates diverge accordingly."
        >
          {causes.length === 0 ? (
            <Empty>Run a batch to populate the scoreboard.</Empty>
          ) : (
            <div className="space-y-3">
              {causes.map((c) => (
                <div key={c.root_cause}>
                  <div className="flex items-baseline justify-between gap-3 text-sm">
                    <span className="font-medium">{c.root_cause}</span>
                    <span className="num text-muted">
                      <span
                        className={
                          c.rate >= 0.3
                            ? "text-green"
                            : c.rate > 0
                              ? "text-amber"
                              : "text-dim"
                        }
                      >
                        {(c.rate * 100).toFixed(0)}%
                      </span>{" "}
                      <span className="text-dim">
                        {c.recovered_records}/{c.records}
                      </span>
                    </span>
                  </div>
                  <div className="mt-1.5">
                    <Bar
                      value={c.rate}
                      tone={c.rate >= 0.3 ? "green" : c.rate > 0 ? "amber" : "red"}
                    />
                  </div>
                  <div className="mt-1 flex justify-between text-[11px] text-dim">
                    <span>{c.contacts} customer contacts</span>
                    <span className="num">
                      {(c.recovered_paise / 100).toLocaleString("en-IN", {
                        style: "currency",
                        currency: "INR",
                        maximumFractionDigits: 0,
                      })}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card
          title={`Guardrails — ${fired.reduce((n, [, held]) => n + held, 0)} records held`}
          hint="Every action the agent wanted to take and was not allowed to. The blocks are the point, not the exceptions."
        >
          {fired.length === 0 ? (
            <Empty>Nothing has been refused yet.</Empty>
          ) : (
            <div className="space-y-3">
              {fired.map(([name, held, refusals]) => (
                <div key={name}>
                  <div className="flex items-baseline justify-between gap-3 text-sm">
                    <span className="font-medium">{name}</span>
                    <span className="num text-red">{held}</span>
                  </div>
                  <div className="mt-1.5">
                    <Bar value={held / maxFired} tone="red" />
                  </div>
                  <div className="mt-1 flex justify-between gap-2 text-[11px] text-dim">
                    <span>{GUARDRAIL_BLURB[name] ?? "guardrail"}</span>
                    <span className="num shrink-0">
                      {refusals} refusals over the run
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Cost of a recovery">
          <div className="space-y-2 text-sm">
            <Row label="Interventions executed" value={board.interventions} />
            <Row label="Customer contacts" value={board.contacts} />
            <Row
              label="Silent retries"
              value={board.silent_retries}
              hint="reached the bank, not the person"
            />
            <Row
              label="Contacts per recovery"
              value={board.contacts_per_recovery.toFixed(2)}
              strong
            />
            <Row label="Human escalations" value={board.escalations} />
            <Row
              label="Outcomes attributed"
              value={board.webhooks_attributed}
              hint="via verified webhooks"
            />
          </div>
        </Card>

        <Card
          className="lg:col-span-2"
          title="Versus a naive strategy"
          hint="Retry everything 3× immediately, message every failure. Same 120 records, same seeded outcomes — only the strategy differs."
        >
          {failed ? (
            <Empty>Baseline unavailable.</Empty>
          ) : !comparison ? (
            <Empty>Computing…</Empty>
          ) : (
            <BaselineTable comparison={comparison} board={board} />
          )}
        </Card>
      </div>

      {comparison && comparison.gap.total.records > 0 && (
        <Card
          title="Where the naive strategy wins, and why"
          hint={`It collects ${comparison.gap.total.display} we do not. Every rupee of that is accounted for below.`}
        >
          <div className="space-y-2">
            {comparison.gap.reasons.map((r) => (
              <div
                key={r.reason}
                className="flex items-center justify-between gap-4 rounded border border-line bg-panel2 px-3 py-2 text-sm"
              >
                <span className="text-muted">{r.label}</span>
                <span className="num shrink-0 tabular-nums">
                  <span className="text-dim">{r.records} rec</span>{" "}
                  <span className="font-medium">{r.display}</span>
                </span>
              </div>
            ))}
          </div>
          <p className="mt-3 text-xs text-muted">
            Most of that gap is money the agent was told not to take — contacts to
            people who opted out, amounts above its authority ceiling, causes the
            policy table refuses to retry. Restraint has a price and this is it,
            stated in rupees.
          </p>
        </Card>
      )}
    </div>
  );
}

function Row({
  label,
  value,
  hint,
  strong,
}: {
  label: string;
  value: string | number;
  hint?: string;
  strong?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-muted">
        {label}
        {hint && <span className="ml-1.5 text-[11px] text-dim">{hint}</span>}
      </span>
      <span className={`num ${strong ? "text-lg font-semibold" : ""}`}>
        {value}
      </span>
    </div>
  );
}

function BaselineTable({
  comparison,
  board,
}: {
  comparison: Comparison;
  board: Scoreboard;
}) {
  const b = comparison.baseline;
  const rows: Array<[string, string, string, "good" | "bad" | "neutral"]> = [
    ["Recovered", b.recovered_display, board.recovered_display, "neutral"],
    [
      "Recovery rate (records)",
      pct(b.record_recovery_rate),
      pct(board.record_recovery_rate),
      "neutral",
    ],
    ["Customer contacts", String(b.contacts), String(board.contacts), "good"],
    [
      "Contacts per recovery",
      b.contacts_per_recovery.toFixed(2),
      board.contacts_per_recovery.toFixed(2),
      "good",
    ],
    ["Contacts to opted-out", String(b.contacts_to_opted_out), "0", "good"],
    ["Contacts on DND", String(b.contacts_to_dnd), "0", "good"],
    ["Contacts in quiet hours", String(b.contacts_in_quiet_hours), "0", "good"],
    [
      "Retries on never-retry causes",
      String(b.retries_against_never_retry),
      "0",
      "good",
    ],
  ];

  return (
    <>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-[11px] uppercase tracking-widest text-dim">
            <th className="pb-2 text-left font-semibold"> </th>
            <th className="pb-2 text-right font-semibold">Naive</th>
            <th className="pb-2 text-right font-semibold">ReclaimAI</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, left, right, kind]) => (
            <tr key={label} className="border-t border-line">
              <td className="py-1.5 text-muted">{label}</td>
              <td className="num py-1.5 text-right text-muted">{left}</td>
              <td
                className={`num py-1.5 text-right font-medium ${
                  kind === "good" ? "text-green" : ""
                }`}
              >
                {right}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-3 text-xs">
        <Badge tone="red">
          {b.compliance_breaches} contacts the guardrail engine refuses
        </Badge>{" "}
        <span className="text-dim">
          — the naive run is not merely less efficient, it is not deployable.
        </span>
      </p>
    </>
  );
}
