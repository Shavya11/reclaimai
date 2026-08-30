"use client";

// The Evidence tab — the README's claims, with the measurements behind them.
//
// Everything here already existed as a `cli` subcommand, which meant nobody
// watching a demo would ever see it. The numbers are read from artifacts
// committed under `evidence/`, produced by real runs of the real harness and
// stamped with their seed, date and commit — the snapshot argument applied to
// measurements. That is stated on each card rather than left to be discovered.
//
// A claim with no artifact renders as missing rather than being hidden. Showing
// less than the whole picture is the failure the void conditions exist to
// prevent, and it would be strange to commit that failure on the tab about not
// committing it.

import { useEffect, useState } from "react";

import { EvidenceClaim, get } from "@/lib/api";
import { Card, Empty, Skeleton } from "@/components/ui";

function rupees(paise: number): string {
  const sign = paise < 0 ? "−" : "+";
  return `${sign}₹${Math.abs(Math.round(paise / 100)).toLocaleString("en-IN")}`;
}

function Row({
  label,
  value,
  note,
  good,
}: {
  label: string;
  value: string;
  note?: string;
  good?: boolean | null;
}) {
  const tone =
    good === true ? "text-green" : good === false ? "text-red" : "text-ink";
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 border-b border-line py-2 last:border-0">
      <span className="min-w-[9rem] text-[12px] text-muted">{label}</span>
      <span className={`num text-[14px] font-semibold ${tone}`}>{value}</span>
      {note && <span className="num text-[10px] text-dim">{note}</span>}
    </div>
  );
}

type Delta = {
  with_ai: number;
  without_ai: number;
  delta: number;
  per_record_ci_95: [number, number];
};

function Ablation({ result }: { result: Record<string, unknown> }) {
  const r = result as {
    population: number;
    layer2_consulted: number;
    layer2_api_calls: number;
    layer2_failure_rate: number;
    headline: string;
    harmful_actions: unknown[];
    recovered_paise: Delta;
    human_escalations: Delta;
    contacts: Delta;
    void?: boolean;
    reason?: string;
  };

  if (r.void) {
    return (
      <p className="rounded-2xl border border-amber/30 bg-amberwash p-3 text-[12px] leading-relaxed text-amber">
        <strong className="font-semibold">No comparison is shown.</strong>{" "}
        {r.reason} There is deliberately no table above this line — a reader who
        skims takes the table, so a warning over one would not be a refusal.
      </p>
    );
  }

  const ci = (d: Delta) =>
    `95% CI [${d.per_record_ci_95[0].toFixed(2)}, ${d.per_record_ci_95[1].toFixed(2)}] per record`;

  return (
    <div>
      <Row
        label="Money recovered"
        value={rupees(r.recovered_paise.delta)}
        note={ci(r.recovered_paise)}
        good={r.recovered_paise.delta > 0}
      />
      <Row
        label="Human escalations"
        value={String(r.human_escalations.delta)}
        note={ci(r.human_escalations)}
        good={r.human_escalations.delta < 0}
      />
      <Row
        label="Contacts sent"
        value={String(r.contacts.delta)}
        note={ci(r.contacts)}
      />
      <Row
        label="Harmful actions"
        value={String(r.harmful_actions?.length ?? 0)}
        note="a cause the truth says must never be chased"
        good={(r.harmful_actions?.length ?? 0) === 0}
      />
      <Row
        label="Cost"
        value={`${r.layer2_api_calls} API calls`}
        note={`${r.layer2_consulted} consultations — the signature cache absorbs the rest`}
      />
      <Row
        label="Unanswered calls"
        value={`${(r.layer2_failure_rate * 100).toFixed(1)}%`}
        note="above 25% no comparison is printed at all"
      />

      <p className="mt-3 rounded-2xl border border-line bg-panel2 p-3 text-[11px] leading-relaxed text-muted">
        Measured over the {r.population} records layer 2 actually answered. Both
        arms are handed the same self-curing customers, so this is what the model
        <em> added</em> rather than what it looks like against a world where
        nobody pays unaided.{" "}
        <strong className="font-semibold text-ink">
          The measurement was built before it was run, and a null result would
          have been published.
        </strong>
      </p>
    </div>
  );
}

function Baseline({ result }: { result: Record<string, unknown> }) {
  const r = result as {
    baseline: Record<string, number | string>;
    ours: Record<string, number | string>;
    gap?: Record<string, unknown>;
  };
  const b = r.baseline ?? {};
  const o = r.ours ?? {};

  return (
    <div>
      <div className="mb-2 flex gap-3 text-[10px] font-semibold uppercase tracking-wide text-dim">
        <span className="min-w-[9rem]" />
        <span className="flex-1">retry everything ×3</span>
        <span className="flex-1">ReclaimAI</span>
      </div>
      {[
        ["Recovered", "recovered_display"],
        ["Records recovered", "recovered_records"],
        ["Contacts sent", "contacts"],
        ["Contacts per recovery", "contacts_per_recovery"],
      ].map(([label, key]) => (
        <div
          key={key}
          className="flex gap-3 border-b border-line py-2 last:border-0"
        >
          <span className="min-w-[9rem] text-[12px] text-muted">{label}</span>
          <span className="num flex-1 text-[13px] text-ink">
            {String(b[key] ?? "—")}
          </span>
          <span className="num flex-1 text-[13px] font-semibold text-ink">
            {String(o[key] ?? "—")}
          </span>
        </div>
      ))}
      <p className="mt-3 rounded-2xl border border-line bg-panel2 p-3 text-[11px] leading-relaxed text-muted">
        Both strategies draw their coin flips from the same seeded stream, keyed
        on (record, attempt). The same record&rsquo;s second attempt succeeds or
        fails identically under both. Nothing separates the runs except what each
        chose to do, when, and to whom.
      </p>
    </div>
  );
}

function Verify({ result }: { result: Record<string, unknown> }) {
  const r = result as {
    checks: Array<{ name: string; status: string; detail: string }>;
    passed: number;
    failed: number;
    pending: number;
  };
  return (
    <div>
      <p className="num mb-3 text-[13px]">
        <span className="font-semibold text-green">{r.passed} passed</span>
        {r.failed > 0 && (
          <span className="ml-2 font-semibold text-red">{r.failed} failed</span>
        )}
        {r.pending > 0 && (
          <span className="ml-2 text-amber">{r.pending} pending</span>
        )}
      </p>
      <ul className="space-y-1">
        {(r.checks ?? []).map((c) => (
          <li key={c.name} className="flex items-start gap-2 py-1">
            <span
              aria-hidden
              className={`num text-[12px] font-bold ${
                c.status === "PASS" ? "text-green" : "text-red"
              }`}
            >
              {c.status === "PASS" ? "✓" : "✕"}
            </span>
            <span className="min-w-0">
              <span className="block text-[12px] text-ink">{c.name}</span>
              <span className="block text-[10px] leading-snug text-dim">
                {c.detail}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const BODIES: Record<
  string,
  (p: { result: Record<string, unknown> }) => React.ReactElement
> = { ablation: Ablation, baseline: Baseline, verify: Verify };

export function Evidence() {
  const [claims, setClaims] = useState<EvidenceClaim[] | null>(null);

  useEffect(() => {
    get<{ claims: EvidenceClaim[] }>("/api/evidence")
      .then((r) => setClaims(r.claims))
      .catch(() => setClaims([]));
  }, []);

  if (claims === null) return <Skeleton className="h-64" />;
  if (!claims.length)
    return (
      <Card title="Evidence">
        <Empty>No evidence endpoint on this API.</Empty>
      </Card>
    );

  return (
    <div className="space-y-4">
      {claims.map((c) => {
        const Body = BODIES[c.name];
        return (
          <Card
            key={c.name}
            title={c.claim}
            hint={
              c.present
                ? `committed ${c.produced_at?.slice(0, 10)} · seed ${c.seed}${
                    c.git_commit ? ` · ${c.git_commit}` : ""
                  }`
                : "not yet measured"
            }
          >
            <p className="mb-4 text-[12px] leading-relaxed text-muted">
              {c.detail}
            </p>

            {c.present && c.result && Body ? (
              <Body result={c.result} />
            ) : (
              <p className="rounded-2xl border border-line bg-panel2 p-3 text-[12px] leading-relaxed text-muted">
                Not measured on this deployment. Run{" "}
                <code className="num text-ink">{c.command}</code> and the result
                is committed under <code className="num text-ink">evidence/</code>
                . It is listed here rather than hidden, because a reader who
                cannot tell a measurement is missing is worse off than one who
                can.
              </p>
            )}

            <p className="num mt-4 text-[10px] text-dim">
              guarded by {c.test} · reproduce with {c.command}
            </p>
          </Card>
        );
      })}
    </div>
  );
}
