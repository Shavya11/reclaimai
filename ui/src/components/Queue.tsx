"use client";

// Screen 2 — the recovery queue.
//
// One row per at-risk record: what it is, what the agent diagnosed, what it
// intends to do next and when. A record sitting still must say why it is
// sitting still — "blocked" with no reason is the same as no information, so
// every deferral carries its guardrail and the time it comes back.

import { useMemo, useState } from "react";

import { RecordRow, fmtTime } from "@/lib/api";
import { Badge, Card, Empty, STATE_TONE } from "@/components/ui";

const FILTERS = [
  { key: "all", label: "All" },
  { key: "blocked", label: "Blocked" },
  { key: "AT_RISK", label: "At risk" },
  { key: "RECOVERED", label: "Recovered" },
  { key: "ESCALATED", label: "Escalated" },
  { key: "CLOSED", label: "Stopped" },
] as const;

export default function Queue({
  records,
  onOpen,
}: {
  records: RecordRow[];
  onOpen: (id: string) => void;
}) {
  const [filter, setFilter] = useState<string>("all");
  const [search, setSearch] = useState("");

  const rows = useMemo(() => {
    let out = records;
    if (filter === "blocked") out = out.filter((r) => r.blocks.length > 0);
    else if (filter !== "all") out = out.filter((r) => r.state === filter);
    const q = search.trim().toUpperCase();
    if (q) {
      out = out.filter(
        (r) =>
          r.id.includes(q) ||
          r.counterparty_id.includes(q) ||
          (r.root_cause ?? "").includes(q),
      );
    }
    return out;
  }, [records, filter, search]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: records.length };
    c.blocked = records.filter((r) => r.blocks.length > 0).length;
    for (const r of records) c[r.state] = (c[r.state] ?? 0) + 1;
    return c;
  }, [records]);

  return (
    <Card
      title="Recovery queue"
      hint="Click any record to open its full decision trail."
      right={
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="record, customer, cause…"
          className="w-56 rounded border border-line bg-panel2 px-2 py-1 text-xs text-ink outline-none placeholder:text-dim focus:border-blue"
        />
      }
    >
      <div className="mb-3 flex flex-wrap gap-1.5">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            className={`rounded border px-2 py-1 text-xs transition ${
              filter === f.key
                ? "border-blue/40 bg-blue/10 text-blue"
                : "border-line bg-panel2 text-muted hover:text-ink"
            }`}
          >
            {f.label}
            <span className="num ml-1.5 text-dim">{counts[f.key] ?? 0}</span>
          </button>
        ))}
      </div>

      {rows.length === 0 ? (
        <Empty>No records match.</Empty>
      ) : (
        <div className="-mx-4 overflow-x-auto">
          <table className="w-full min-w-[900px] text-sm">
            <thead>
              <tr className="border-b border-line text-[11px] uppercase tracking-widest text-dim">
                <th className="px-4 py-2 text-left font-semibold">Record</th>
                <th className="px-2 py-2 text-right font-semibold">Amount</th>
                <th className="px-2 py-2 text-left font-semibold">Root cause</th>
                <th className="px-2 py-2 text-left font-semibold">State</th>
                <th className="px-2 py-2 text-left font-semibold">Next action</th>
                <th className="px-4 py-2 text-left font-semibold">Why it waits</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.id}
                  onClick={() => onOpen(r.id)}
                  className="cursor-pointer border-b border-line/60 hover:bg-panel2"
                >
                  <td className="px-4 py-2">
                    <div className="num font-medium">{r.id}</div>
                    <div className="text-[11px] text-dim">
                      {r.counterparty_id} · {r.leak_type}
                      {r.issuer_bank ? ` · ${r.issuer_bank}` : ""}
                    </div>
                  </td>
                  <td className="num px-2 py-2 text-right font-medium">
                    {r.amount_display}
                  </td>
                  <td className="px-2 py-2">
                    <span className="text-xs">{r.root_cause ?? "—"}</span>
                    {r.last_policy_ref && (
                      <div className="num text-[11px] text-dim">
                        {r.last_policy_ref}
                      </div>
                    )}
                  </td>
                  <td className="px-2 py-2">
                    <Badge tone={STATE_TONE[r.state] ?? "plain"}>{r.state}</Badge>
                    {r.recovered_paise > 0 && (
                      <div className="num mt-1 text-[11px] text-green">
                        +{(r.recovered_paise / 100).toLocaleString("en-IN")}
                      </div>
                    )}
                  </td>
                  <td className="px-2 py-2">
                    <div className="text-xs">
                      {r.last_action ?? "—"}
                      {r.attempts > 0 && (
                        <span className="text-dim"> · attempt {r.attempts}</span>
                      )}
                    </div>
                    <div className="num text-[11px] text-dim">
                      {r.next_action_at
                        ? `due ${fmtTime(r.next_action_at)}`
                        : fmtTime(r.last_action_at)}
                    </div>
                  </td>
                  <td className="px-4 py-2">
                    {r.blocks.length === 0 ? (
                      <span className="text-xs text-dim">—</span>
                    ) : (
                      <div className="space-y-1">
                        {r.blocks.slice(0, 2).map((b, i) => (
                          <div key={i} className="flex items-start gap-1.5">
                            <Badge tone="red">{b.guardrail}</Badge>
                            <span
                              className="line-clamp-1 text-[11px] text-muted"
                              title={b.reason}
                            >
                              {b.deferred_until
                                ? `retries ${fmtTime(b.deferred_until)}`
                                : b.reason}
                            </span>
                          </div>
                        ))}
                        {r.blocks.length > 2 && (
                          <span className="text-[11px] text-dim">
                            +{r.blocks.length - 2} more
                          </span>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
