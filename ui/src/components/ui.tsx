// The primitives the four screens are built from.
//
// Written by hand rather than pulled from shadcn/ui: the whole surface is a
// card, a badge, a stat, a button and a table, and a component library would
// have been more install than code.
//
// Card keeps the exact props it had before the redesign, so the recovery queue
// and the audit trail inherit the new visual language without being touched.

import type { ButtonHTMLAttributes, ReactNode } from "react";

import { IconArrowUpRight } from "@/components/icons";

export function Card({
  title,
  hint,
  right,
  children,
  className = "",
  tone = "plain",
  bodyClass = "",
}: {
  title?: string;
  hint?: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  tone?: "plain" | "deep";
  bodyClass?: string;
}) {
  const deep = tone === "deep";
  return (
    <section
      className={`flex flex-col rounded-3xl border ${
        deep
          ? "border-transparent bg-greendeep"
          : "border-line bg-panel shadow-[var(--shadow-card)]"
      } ${className}`}
    >
      {(title || right) && (
        <header className="flex items-start justify-between gap-4 px-5 pt-5">
          <div className="min-w-0">
            {title && (
              <h2
                className={`text-[15px] font-semibold tracking-tight ${
                  deep ? "on-deep" : "text-ink"
                }`}
              >
                {title}
              </h2>
            )}
            {hint && (
              <p
                className={`mt-1 max-w-prose text-[11px] leading-relaxed ${
                  deep ? "text-ondeep/70" : "text-dim"
                }`}
              >
                {hint}
              </p>
            )}
          </div>
          {right && <div className="shrink-0">{right}</div>}
        </header>
      )}
      <div className={`flex-1 p-5 ${title || right ? "pt-4" : ""} ${bodyClass}`}>
        {children}
      </div>
    </section>
  );
}

const TONES: Record<string, string> = {
  green: "border-green/25 bg-greenwash text-green",
  red: "border-red/25 bg-redwash text-red",
  amber: "border-amber/25 bg-amberwash text-amber",
  blue: "border-blue/25 bg-blue/10 text-blue",
  violet: "border-violet/25 bg-violet/10 text-violet",
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
      className={`inline-flex items-center whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium ${
        TONES[tone] ?? TONES.plain
      }`}
    >
      {children}
    </span>
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger" | "chip";
  icon?: ReactNode;
  busy?: boolean;
};

/* Every clickable thing routes through here, so the cursor, the focus ring,
   the disabled state and the 200ms colour transition are decided once. */
export function Button({
  variant = "secondary",
  icon,
  busy,
  children,
  className = "",
  disabled,
  ...rest
}: ButtonProps) {
  const base =
    "inline-flex cursor-pointer items-center justify-center gap-1.5 rounded-full font-medium transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-50";
  const size = variant === "chip" ? "px-2.5 py-1.5 text-[11px]" : "px-4 py-2.5 text-[13px]";
  const look = {
    primary: "bg-greendeep text-ondeep hover:bg-green",
    secondary: "border border-linestrong bg-panel text-ink hover:bg-panel2",
    ghost: "text-muted hover:bg-panel2 hover:text-ink",
    danger: "border border-red/30 bg-redwash text-red hover:border-red/60",
    chip: "num border border-line bg-panel text-muted hover:border-linestrong hover:text-ink",
  }[variant];

  return (
    <button
      {...rest}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={`${base} ${size} ${look} ${className}`}
    >
      {busy ? <Spinner /> : icon}
      {children}
    </button>
  );
}

function Spinner() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="h-3.5 w-3.5 animate-spin"
      aria-hidden="true"
    >
      <circle
        cx="12"
        cy="12"
        r="9"
        fill="none"
        stroke="currentColor"
        strokeWidth="3"
        opacity="0.25"
      />
      <path
        d="M12 3a9 9 0 0 1 9 9"
        fill="none"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

/* The four numbers a judge should be able to read from across the room. The
   first one is filled rather than outlined, because "money at risk" is the
   premise everything else answers. */
export function Stat({
  label,
  value,
  sub,
  tone = "ink",
  accent = false,
  onOpen,
  openLabel,
}: {
  label: string;
  value: string;
  sub?: ReactNode;
  tone?: "ink" | "green" | "red" | "amber";
  accent?: boolean;
  onOpen?: () => void;
  openLabel?: string;
}) {
  const colour = accent
    ? "on-deep"
    : tone === "green"
      ? "text-green"
      : tone === "red"
        ? "text-red"
        : tone === "amber"
          ? "text-amber"
          : "text-ink";

  return (
    <div
      className={`flex flex-col justify-between rounded-3xl border p-5 transition-shadow duration-200 ${
        accent
          ? "border-transparent bg-greendeep"
          : "border-line bg-panel shadow-[var(--shadow-card)] hover:shadow-[var(--shadow-lift)]"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <h3
          className={`text-[13px] font-medium ${accent ? "text-ondeep/80" : "text-muted"}`}
        >
          {label}
        </h3>
        {onOpen && (
          <button
            type="button"
            onClick={onOpen}
            aria-label={openLabel ?? `Open ${label}`}
            title={openLabel ?? `Open ${label}`}
            className={`grid h-8 w-8 shrink-0 cursor-pointer place-items-center rounded-full border transition-colors duration-200 ${
              accent
                ? "border-ondeep/25 text-ondeep hover:bg-ondeep/15"
                : "border-line text-muted hover:border-linestrong hover:text-ink"
            }`}
          >
            <IconArrowUpRight className="h-4 w-4" />
          </button>
        )}
      </div>
      <p
        className={`num mt-4 text-[30px] font-bold leading-none tracking-tight sm:text-[34px] ${colour}`}
      >
        {value}
      </p>
      {sub && (
        <p
          className={`mt-2 text-[11px] leading-relaxed ${
            accent ? "text-ondeep/70" : "text-dim"
          }`}
        >
          {sub}
        </p>
      )}
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
        className={`h-full rounded-full ${colour} transition-all duration-500`}
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

/* Reserves the space the real content will take, so nothing jumps when the
   fetch lands. */
export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton rounded-2xl ${className}`} aria-hidden="true" />;
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
