"use client";

// The charts.
//
// Hand-built in SVG and CSS rather than pulled from a charting library. Three
// reasons, in order of weight: the hatched fills that carry "not pursued" are
// custom anyway; a static export should not ship 90kB of runtime to draw nine
// bars; and every chart here needs a text alternative, which is easier to keep
// truthful when the same array renders both.
//
// Encoding is consistent across all of them:
//   solid green   money or records recovered
//   amber         still in flight
//   hatched       deliberately not pursued, or refused
// Colour is never the only channel — hatching and a label carry it too.

import type { ReactNode } from "react";

// Short axis labels. Full names still reach the screen reader and the tooltip;
// only the tick under the bar is abbreviated.
export const CAUSE_SHORT: Record<string, string> = {
  INSUFFICIENT_FUNDS: "Funds",
  BANK_DOWNTIME: "Bank",
  EXPIRED_INSTRUMENT: "Expired",
  INVALID_INSTRUMENT: "Invalid",
  AUTH_DROPOFF: "Auth",
  LIMIT_EXCEEDED: "Limit",
  RISK_DECLINE: "Risk",
  POLICY_BLOCK: "Policy",
  CART_ABANDONMENT: "Cart",
  MANDATE_REVOKED: "Mandate",
  TECHNICAL_ERROR: "Technical",
  UNKNOWN: "Unknown",
};

const HATCH =
  "repeating-linear-gradient(135deg, currentColor 0 1.5px, transparent 1.5px 5px)";

/** The table every chart falls back to — collapsed, but real markup. */
export function ChartTable({
  caption,
  head,
  rows,
}: {
  caption: string;
  head: string[];
  rows: (string | number)[][];
}) {
  return (
    <details className="group mt-4">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 rounded-md text-[11px] font-medium text-dim transition-colors duration-200 hover:text-ink">
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
          className="h-3 w-3 transition-transform duration-200 group-open:rotate-90"
        >
          <path d="m9 6 6 6-6 6" />
        </svg>
        View as table
      </summary>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-left text-xs">
          <caption className="sr-only">{caption}</caption>
          <thead>
            <tr className="text-[10px] uppercase tracking-wider text-dim">
              {head.map((h, i) => (
                <th
                  key={h}
                  scope="col"
                  className={`pb-1.5 font-semibold ${i === 0 ? "" : "text-right"}`}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="text-muted">
            {rows.map((r) => (
              <tr key={String(r[0])} className="border-t border-line">
                {r.map((cell, i) => (
                  <td
                    key={i}
                    className={`py-1 ${i === 0 ? "pr-3" : "num text-right"}`}
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

export function Legend({
  items,
}: {
  items: Array<{ label: string; tone: "green" | "amber" | "hatch"; note?: string }>;
}) {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-1.5 text-[11px]">
          <span
            aria-hidden="true"
            className={`h-2.5 w-2.5 shrink-0 rounded-full ${
              i.tone === "green"
                ? "bg-green"
                : i.tone === "amber"
                  ? "bg-amber"
                  : "text-linestrong ring-1 ring-linestrong"
            }`}
            style={i.tone === "hatch" ? { backgroundImage: HATCH } : undefined}
          />
          <span className="text-muted">{i.label}</span>
          {i.note && <span className="num text-dim">{i.note}</span>}
        </li>
      ))}
    </ul>
  );
}

/* --- the money band -------------------------------------------------------
   One bar, the whole batch. Recovered, still in flight, and written off always
   sum to the money detected, so the bar is fully partitioned by construction —
   if it ever looks short, the scoreboard has stopped balancing. */

export function MoneySplit({
  segments,
}: {
  segments: Array<{
    label: string;
    paise: number;
    display: string;
    tone: "green" | "amber" | "hatch";
  }>;
}) {
  const total = segments.reduce((n, s) => n + s.paise, 0) || 1;
  return (
    <figure
      role="img"
      aria-label={segments
        .map(
          (s) =>
            `${s.label} ${s.display}, ${((s.paise / total) * 100).toFixed(0)} percent`,
        )
        .join("; ")}
    >
      <div className="flex h-3 w-full gap-1 overflow-hidden rounded-full">
        {segments.map((s) => (
          <div
            key={s.label}
            title={`${s.label} — ${s.display}`}
            style={{
              width: `${Math.max((s.paise / total) * 100, s.paise > 0 ? 1.5 : 0)}%`,
              ...(s.tone === "hatch" ? { backgroundImage: HATCH } : {}),
            }}
            className={`rounded-full transition-all duration-500 ${
              s.tone === "green"
                ? "bg-green"
                : s.tone === "amber"
                  ? "bg-amber"
                  : "bg-panel2 text-linestrong ring-1 ring-inset ring-linestrong"
            }`}
          />
        ))}
      </div>
      <figcaption className="mt-2.5 flex flex-wrap gap-x-5 gap-y-1.5">
        {segments.map((s) => (
          <span key={s.label} className="flex items-baseline gap-1.5 text-[11px]">
            <span
              aria-hidden="true"
              className={`h-2 w-2 translate-y-px rounded-full ${
                s.tone === "green"
                  ? "bg-green"
                  : s.tone === "amber"
                    ? "bg-amber"
                    : "text-linestrong ring-1 ring-linestrong"
              }`}
              style={s.tone === "hatch" ? { backgroundImage: HATCH } : undefined}
            />
            <span className="text-muted">{s.label}</span>
            <span className="num font-semibold text-ink">{s.display}</span>
            <span className="num text-dim">
              {((s.paise / total) * 100).toFixed(0)}%
            </span>
          </span>
        ))}
      </figcaption>
    </figure>
  );
}

/* --- root cause column chart ---------------------------------------------
   Height is volume, fill is outcome. A tall mostly-hatched column is a big
   pile of money the policy table decided not to chase, which is a different
   failure from a short one — and the two must not look alike. */

export type CauseDatum = {
  cause: string;
  records: number;
  recovered: number;
  rate: number;
  recoveredPaise: number;
  contacts: number;
};

export function CauseChart({ data }: { data: CauseDatum[] }) {
  const maxRecords = Math.max(1, ...data.map((d) => d.records));
  const best = data.reduce(
    (a, b) => (b.rate > a.rate ? b : a),
    data[0] ?? { rate: -1, cause: "" },
  );

  return (
    <figure>
      <div
        role="img"
        aria-label={`Records and recovery rate by root cause. ${data
          .map(
            (d) =>
              `${d.cause}: ${d.recovered} of ${d.records} recovered, ${(d.rate * 100).toFixed(0)} percent`,
          )
          .join(". ")}`}
        className="flex h-56 items-end gap-2 sm:gap-3"
      >
        {data.map((d) => {
          const h = Math.max(8, (d.records / maxRecords) * 100);
          const fill = d.records ? (d.recovered / d.records) * 100 : 0;
          const isBest = d.cause === best.cause && d.rate > 0;
          return (
            <div
              key={d.cause}
              className="group flex h-full min-w-0 flex-1 flex-col items-center justify-end"
            >
              {isBest && (
                <span className="num mb-1.5 rounded-full bg-greendeep px-1.5 py-0.5 text-[10px] font-semibold text-ondeep">
                  {(d.rate * 100).toFixed(0)}%
                </span>
              )}
              <div
                title={`${d.cause} — ${d.recovered} of ${d.records} records recovered (${(d.rate * 100).toFixed(0)}%)`}
                style={{ height: `${h}%` }}
                className="relative flex w-full max-w-14 flex-col justify-end overflow-hidden rounded-full bg-panel2 text-linestrong ring-1 ring-inset ring-line transition-shadow duration-200 group-hover:ring-linestrong"
              >
                <span
                  aria-hidden="true"
                  className="absolute inset-0"
                  style={{ backgroundImage: HATCH }}
                />
                <span
                  aria-hidden="true"
                  style={{ height: `${fill}%` }}
                  className="relative w-full rounded-full bg-green transition-all duration-500"
                />
              </div>
              <span className="num mt-2 text-[10px] font-semibold text-muted">
                {d.records}
              </span>
              <span
                className="w-full truncate text-center text-[10px] text-dim"
                title={d.cause}
              >
                {CAUSE_SHORT[d.cause] ?? d.cause}
              </span>
            </div>
          );
        })}
      </div>

      <div className="mt-4 border-t border-line pt-3">
        <Legend
          items={[
            { label: "Recovered", tone: "green" },
            { label: "Not recovered", tone: "hatch" },
          ]}
        />
      </div>

      <ChartTable
        caption="Records and recovery rate by root cause"
        head={["Root cause", "Records", "Recovered", "Rate", "Contacts"]}
        rows={data.map((d) => [
          d.cause,
          d.records,
          d.recovered,
          `${(d.rate * 100).toFixed(0)}%`,
          d.contacts,
        ])}
      />
    </figure>
  );
}

/* --- resolution gauge ----------------------------------------------------- */

const polar = (cx: number, cy: number, r: number, deg: number) => {
  const a = ((deg - 90) * Math.PI) / 180;
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)] as const;
};

const arc = (cx: number, cy: number, r: number, from: number, to: number) => {
  const [x1, y1] = polar(cx, cy, r, from);
  const [x2, y2] = polar(cx, cy, r, to);
  return `M ${x1} ${y1} A ${r} ${r} 0 ${to - from > 180 ? 1 : 0} 1 ${x2} ${y2}`;
};

export function ResolutionGauge({
  segments,
  centre,
  centreLabel,
}: {
  segments: Array<{ label: string; value: number; tone: "green" | "amber" | "hatch" }>;
  centre: string;
  centreLabel: string;
}) {
  const total = segments.reduce((n, s) => n + s.value, 0) || 1;
  const SWEEP = 260;
  const START = -130;
  const GAP = 3;

  // Prefix sum rather than a running cursor, so nothing outside the map is
  // mutated while React is rendering.
  const spans = segments.map((s) => (s.value / total) * SWEEP);
  const drawn = segments.map((s, i) => {
    const from = START + spans.slice(0, i).reduce((a, b) => a + b, 0);
    return { ...s, from, to: from + spans[i], span: spans[i] };
  });

  return (
    <figure className="flex flex-col items-center">
      <div className="relative w-full max-w-[248px]">
        <svg
          viewBox="0 0 200 168"
          className="w-full"
          role="img"
          aria-label={`${centreLabel}: ${centre}. ${drawn
            .map((s) => `${s.label} ${s.value}`)
            .join(", ")}.`}
        >
          <defs>
            <pattern
              id="gauge-hatch"
              width="6"
              height="6"
              patternTransform="rotate(45)"
              patternUnits="userSpaceOnUse"
            >
              <line
                x1="0"
                y1="0"
                x2="0"
                y2="6"
                stroke="var(--line-strong)"
                strokeWidth="2"
              />
            </pattern>
          </defs>

          {/* Track, so a nearly-empty gauge still reads as a gauge. */}
          <path
            d={arc(100, 100, 72, START, START + SWEEP)}
            fill="none"
            stroke="var(--panel-2)"
            strokeWidth="22"
            strokeLinecap="round"
          />

          {drawn
            .filter((s) => s.span > 0.5)
            .map((s) => (
              <path
                key={s.label}
                d={arc(
                  100,
                  100,
                  72,
                  s.from + GAP / 2,
                  Math.max(s.from + GAP / 2 + 0.1, s.to - GAP / 2),
                )}
                fill="none"
                strokeWidth="22"
                strokeLinecap="round"
                stroke={
                  s.tone === "green"
                    ? "var(--green)"
                    : s.tone === "amber"
                      ? "var(--amber)"
                      : "url(#gauge-hatch)"
                }
              >
                <title>{`${s.label}: ${s.value}`}</title>
              </path>
            ))}

          <text
            x="100"
            y="98"
            textAnchor="middle"
            className="num"
            fontSize="34"
            fontWeight="700"
            fill="var(--ink)"
          >
            {centre}
          </text>
          <text
            x="100"
            y="118"
            textAnchor="middle"
            fontSize="11"
            fill="var(--dim)"
          >
            {centreLabel}
          </text>
        </svg>
      </div>

      <figcaption className="mt-1 w-full">
        <Legend
          items={drawn.map((s) => ({
            label: s.label,
            tone: s.tone,
            note: String(s.value),
          }))}
        />
      </figcaption>
    </figure>
  );
}

/* --- horizontal rail, used for guardrail refusals ------------------------- */

export function RailBar({
  label,
  note,
  value,
  valueText,
  max,
  suffix,
  tone = "red",
}: {
  label: string;
  note?: string;
  /** Drives the bar length only. */
  value: number;
  /** What the reader sees. Defaults to `value`, which is wrong for anything
      held in paise — pass the formatted string there. */
  valueText?: string;
  max: number;
  suffix?: string;
  tone?: "red" | "green" | "amber";
}) {
  const colour =
    tone === "red" ? "bg-red" : tone === "amber" ? "bg-amber" : "bg-green";
  const text =
    tone === "red" ? "text-red" : tone === "amber" ? "text-amber" : "text-green";
  return (
    <li className="group">
      <div className="flex items-baseline justify-between gap-3">
        <span className="truncate text-[13px] font-medium text-ink" title={label}>
          {label}
        </span>
        <span className={`num shrink-0 text-[13px] font-semibold ${text}`}>
          {valueText ?? value}
          {suffix && <span className="ml-1 text-[10px] text-dim">{suffix}</span>}
        </span>
      </div>
      <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-panel2">
        <div
          className={`h-full rounded-full ${colour} transition-all duration-500`}
          style={{ width: `${Math.max(2, (value / Math.max(1, max)) * 100)}%` }}
        />
      </div>
      {note && <p className="mt-1 truncate text-[11px] text-dim">{note}</p>}
    </li>
  );
}

/* --- naive versus ours ----------------------------------------------------
   Paired bars rather than a two-column table. The argument is not "these
   numbers differ", it is "look how far apart they are", and a table cannot
   make that point across a room. */

export type PairRow = {
  label: string;
  naive: number;
  ours: number;
  naiveText: string;
  oursText: string;
  /** Lower is better, so a short green bar is the win. */
  lowerIsBetter: boolean;
  note?: string;
};

export function PairedBars({ rows }: { rows: PairRow[] }) {
  return (
    <figure>
      <div className="space-y-3.5">
        {rows.map((r) => {
          const max = Math.max(r.naive, r.ours, 1);
          const wins = r.lowerIsBetter ? r.ours <= r.naive : r.ours >= r.naive;
          return (
            <div key={r.label}>
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-[13px] font-medium text-ink">
                  {r.label}
                </span>
                {r.note && (
                  <span className="text-[11px] text-dim">{r.note}</span>
                )}
              </div>
              <div className="mt-1.5 space-y-1">
                <BarRow
                  who="Naive"
                  text={r.naiveText}
                  width={(r.naive / max) * 100}
                  variant="hatch"
                />
                <BarRow
                  who="ReclaimAI"
                  text={r.oursText}
                  width={(r.ours / max) * 100}
                  variant={wins ? "green" : "amber"}
                />
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-4 border-t border-line pt-3">
        <Legend
          items={[
            { label: "Naive strategy", tone: "hatch" },
            { label: "ReclaimAI", tone: "green" },
          ]}
        />
      </div>

      <ChartTable
        caption="Naive strategy compared with ReclaimAI over the same batch"
        head={["Metric", "Naive", "ReclaimAI"]}
        rows={rows.map((r) => [r.label, r.naiveText, r.oursText])}
      />
    </figure>
  );
}

function BarRow({
  who,
  text,
  width,
  variant,
}: {
  who: string;
  text: string;
  width: number;
  variant: "hatch" | "green" | "amber";
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-[68px] shrink-0 text-[10px] uppercase tracking-wider text-dim">
        {who}
      </span>
      <div className="h-4 min-w-0 flex-1 overflow-hidden rounded-full bg-panel2">
        <div
          style={{
            width: `${Math.max(1.5, width)}%`,
            ...(variant === "hatch" ? { backgroundImage: HATCH } : {}),
          }}
          className={`h-full rounded-full transition-all duration-500 ${
            variant === "green"
              ? "bg-green"
              : variant === "amber"
                ? "bg-amber"
                : "bg-panel2 text-linestrong ring-1 ring-inset ring-linestrong"
          }`}
        />
      </div>
      <span className="num w-16 shrink-0 text-right text-[11px] font-semibold text-ink">
        {text}
      </span>
    </div>
  );
}

/* --- shared card sub-heading ---------------------------------------------- */

export function ChartNote({ children }: { children: ReactNode }) {
  return <p className="mt-3 text-[11px] leading-relaxed text-dim">{children}</p>;
}
