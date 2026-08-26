// The handful of primitives the three screens are built from.
//
// Written by hand rather than pulled from shadcn/ui: the whole surface is a
// card, a badge, a stat and a table, and a component library would have been
// more install than code. The visual language is the same.

import type { ReactNode } from "react";

export function Card({
  title,
  hint,
  right,
  children,
  className = "",
}: {
  title?: string;
  hint?: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-lg border border-line bg-panel ${className}`}
    >
      {(title || right) && (
        <header className="flex items-baseline justify-between gap-4 border-b border-line px-4 py-3">
          <div>
            {title && (
              <h2 className="text-xs font-semibold uppercase tracking-widest text-muted">
                {title}
              </h2>
            )}
            {hint && <p className="mt-1 text-xs text-dim">{hint}</p>}
          </div>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

const TONES: Record<string, string> = {
  green: "border-green/30 bg-green/10 text-green",
  red: "border-red/30 bg-red/10 text-red",
  amber: "border-amber/30 bg-amber/10 text-amber",
  blue: "border-blue/30 bg-blue/10 text-blue",
  violet: "border-violet/30 bg-violet/10 text-violet",
  plain: "border-line bg-panel2 text-muted",
};

export function Badge({
  tone = "plain",
  children,
  title,
}: {
  tone?: keyof typeof TONES | string;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] font-medium ${
        TONES[tone] ?? TONES.plain
      }`}
    >
      {children}
    </span>
  );
}

export function Stat({
  label,
  value,
  sub,
  tone = "ink",
}: {
  label: string;
  value: string;
  sub?: ReactNode;
  tone?: "ink" | "green" | "red" | "amber";
}) {
  const colour =
    tone === "green"
      ? "text-green"
      : tone === "red"
        ? "text-red"
        : tone === "amber"
          ? "text-amber"
          : "text-ink";
  return (
    <div className="rounded-lg border border-line bg-panel px-4 py-3">
      <div className="text-[11px] font-semibold uppercase tracking-widest text-dim">
        {label}
      </div>
      <div className={`num mt-1.5 text-2xl font-semibold ${colour}`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-muted">{sub}</div>}
    </div>
  );
}

export function Bar({
  value,
  tone = "green",
}: {
  value: number;
  tone?: "green" | "red" | "blue" | "amber";
}) {
  const colour = {
    green: "bg-green",
    red: "bg-red",
    blue: "bg-blue",
    amber: "bg-amber",
  }[tone];
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-panel2">
      <div
        className={`h-full rounded-full ${colour}`}
        style={{ width: `${Math.max(0, Math.min(100, value * 100))}%` }}
      />
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="px-4 py-10 text-center text-sm text-dim">{children}</div>
  );
}

// State and cause colouring, defined once so the same word never means two
// different colours on two different screens.
export const STATE_TONE: Record<string, string> = {
  RECOVERED: "green",
  AT_RISK: "amber",
  IN_PROGRESS: "blue",
  ESCALATED: "violet",
  CLOSED: "plain",
  UNRECOVERABLE: "red",
};

export const STAGE_TONE: Record<string, string> = {
  DETECT: "plain",
  DIAGNOSE: "blue",
  DECIDE: "violet",
  GUARDRAIL: "amber",
  EXECUTE: "blue",
  OUTCOME: "green",
};
