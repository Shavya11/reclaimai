"use client";

// Screen 2 — the recovery queue.
//
// One row per at-risk record: what it is, what the agent diagnosed, what it
// intends to do next and when. A record sitting still must say why it is
// sitting still — "blocked" with no reason is the same as no information, so
// every deferral carries its guardrail and the time it comes back.
//
// Search and filter are owned by the shell rather than by this component: the
// header search box and the drill-through arrows on the dashboard both land
// here, and two sources of truth for "what is on screen" would have been one
// too many.

import { useMemo } from "react";

import { RecordRow, fmtTime } from "@/lib/api";
import { Badge, Card, Empty, STATE_TONE } from "@/components/ui";

const FILTERS = [
  { key: "all", label: "All" },
  { key: "blocked", label: "Blocked" },
  { key: "AT_RISK", label: "At risk" },
  { key: "PROMISED", label: "Promised" },
  { key: "OVERDUE_INVOICE", label: "Invoices" },
  { key: "RECOVERED", label: "Recovered" },
  { key: "ESCALATED", label: "Escalated" },
  { key: "CLOSED", label: "Stopped" },
] as const;

export default function Queue({
  records,
  onOpen,
  search,
  onSearch,
  filter,
  onFilter,
}: {
  records: RecordRow[];
  onOpen: (id: string) => void;
  search: string;
  onSearch: (v: string) => void;
  filter: string;
  onFilter: (v: string) => void;
}) {
  const rows = useMemo(() => {
    let out = records;
    if (filter === "blocked") out = out.filter((r) => r.blocks.length > 0);
    // The filter bar mixes two axes on purpose. A demo is driven by "show me
    // the invoices" as readily as by "show me what recovered", and forcing
    // those into separate controls costs a click at the exact moment somebody
    // is watching.
    else if (filter === "OVERDUE_INVOICE")
      out = out.filter((r) => r.leak_type === "OVERDUE_INVOICE");
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

  const shown = rows.reduce((n, r) => n + r.amount_paise, 0);

  return (
    <Card
      title={`${rows.length} of ${records.length} records`}
      hint="Click any record to open its full decision trail."
      right={
        <div className="text-right">
          <p className="num text-[22px] font-bold leading-none text-ink">
            {(shown / 100).toLocaleString("en-IN", {
              style: "currency",
              currency: "INR",
              maximumFractionDigits: 0,
            })}
          </p>
          <p className="mt-1 text-[11px] text-dim">shown</p>
        </div>
      }
    >
      <div className="mb-4 flex flex-wrap items-center gap-1.5">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            onClick={() => onFilter(f.key)}
            aria-pressed={filter === f.key}
            className={`cursor-pointer rounded-full border px-3 py-1.5 text-[12px] font-medium transition-colors duration-200 ${
              filter === f.key
                ? "border-green/30 bg-greenwash text-green"
                : "border-line bg-panel text-muted hover:border-linestrong hover:text-ink"
            }`}
          >
            {f.label}
            <span className="num ml-1.5 text-dim">{counts[f.key] ?? 0}</span>
          </button>
        ))}

        {search && (
          <button
            type="button"
            onClick={() => onSearch("")}
            className="ml-auto cursor-pointer rounded-full border border-line px-3 py-1.5 text-[12px] text-muted transition-colors duration-200 hover:text-ink"
          >
            Clear “{search}”
          </button>
        )}
      </div>

      {rows.length === 0 ? (
        <Empty>No records match.</Empty>
      ) : (
        <div className="-mx-5 overflow-x-auto">
          <table className="w-full min-w-[900px] text-[13px]">
            <thead>
              <tr className="border-b border-line text-[10px] uppercase tracking-wider text-dim">
                <th scope="col" className="px-5 py-2.5 text-left font-semibold">
                  Record
                </th>
                <th scope="col" className="px-2 py-2.5 text-right font-semibold">
                  Amount
                </th>
                <th scope="col" className="px-2 py-2.5 text-left font-semibold">
                  Root cause
                </th>
                <th scope="col" className="px-2 py-2.5 text-left font-semibold">
                  State
                </th>
                <th scope="col" className="px-2 py-2.5 text-left font-semibold">
                  Next action
                </th>
                <th scope="col" className="px-5 py-2.5 text-left font-semibold">
                  Why it waits
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.id}
                  onClick={() => onOpen(r.id)}
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onOpen(r.id);
                    }
                  }}
                  className="cursor-pointer border-b border-line/60 transition-colors duration-200 hover:bg-panel2"
                >
                  <td className="px-5 py-2.5">
                    <div className="num font-medium">{r.id}</div>
                    <div className="text-[11px] text-dim">
                      {r.counterparty_id} · {r.leak_type}
                      {r.issuer_bank ? ` · ${r.issuer_bank}` : ""}
                    </div>
                  </td>
                  <td className="num px-2 py-2.5 text-right font-semibold">
                    {r.amount_display}
                  </td>
                  <td className="px-2 py-2.5">
                    <span className="text-[12px]">{r.root_cause ?? "—"}</span>
                    {r.last_policy_ref && (
                      <div className="num text-[11px] text-dim">
                        {r.last_policy_ref}
                      </div>
                    )}
                  </td>
                  <td className="px-2 py-2.5">
                    <Badge tone={STATE_TONE[r.state] ?? "plain"}>{r.state}</Badge>
                    {r.recovered_paise > 0 && (
                      <div className="num mt-1 text-[11px] font-medium text-green">
                        +{(r.recovered_paise / 100).toLocaleString("en-IN")}
                      </div>
                    )}
                  </td>
                  <td className="px-2 py-2.5">
                    {/* Nothing scheduled and nothing done is one dash, not two
                        stacked ones. */}
                    {!r.last_action && !r.next_action_at && !r.last_action_at ? (
                      <span className="text-[12px] text-dim">—</span>
                    ) : (
                      <>
                        <div className="text-[12px]">
                          {r.last_action ?? "—"}
                          {r.attempts > 0 && (
                            <span className="text-dim">
                              {" "}
                              · attempt {r.attempts}
                            </span>
                          )}
                        </div>
                        <div className="num text-[11px] text-dim">
                          {r.next_action_at
                            ? `due ${fmtTime(r.next_action_at)}`
                            : fmtTime(r.last_action_at)}
                        </div>
                      </>
                    )}
                  </td>
                  <td className="px-5 py-2.5">
                    {r.blocks.length === 0 ? (
                      <span className="text-[12px] text-dim">—</span>
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
