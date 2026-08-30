"use client";

// The trace strip — one card per stage, filling left to right.
//
// The reason this component exists is the badge. CLAUDE.md's one rule is that
// the model never touches money: it produces a label, a deterministic table
// turns that label into an action, and a deterministic gate decides whether the
// action may fire. Every other screen in this dashboard asks you to take that on
// trust. Here you can see it — the model badge appears on at most one card, and
// on a record layer 1 resolved it appears on none.
//
// So `decided_by` is rendered louder than the stage name. A reader who takes
// nothing else from this screen should still leave knowing which decisions a
// language model was allowed to make.

import { ReactNode, useState } from "react";

import { TraceStage } from "@/lib/api";

// Deliberately not a rainbow. Three families — the model, the deterministic
// halves, and the runner — because the distinction being drawn is three-way and
// five colours would imply five kinds of thing.
const DECIDER: Record<
  TraceStage["decided_by"],
  { label: string; glyph: string; className: string; blurb: string }
> = {
  model: {
    label: "model",
    glyph: "AI",
    className: "border-amber/40 bg-amberwash text-amber",
    blurb:
      "A language model produced a label from a closed set, and a confidence. It chose nothing else.",
  },
  table: {
    label: "table",
    glyph: "=",
    className: "border-line bg-panel2 text-muted",
    blurb:
      "A deterministic lookup. Same input, same output, every time — no model was consulted.",
  },
  gate: {
    label: "gate",
    glyph: "●",
    className: "border-green/40 bg-greenwash text-green",
    blurb:
      "The guardrail engine. Fourteen rules, each of which can refuse. Failure is a BLOCK, never an exception.",
  },
  detector: {
    label: "detector",
    glyph: "■",
    className: "border-line bg-panel2 text-muted",
    blurb: "A plugin claimed this record and gave it a leak type.",
  },
  runner: {
    label: "runner",
    glyph: "→",
    className: "border-line bg-panel2 text-muted",
    blurb: "The batch orchestrator carrying out a decision already made above.",
  },
};

function outcomeTone(output: string): string {
  const o = output.toUpperCase();
  if (o === "ALLOWED" || o === "EXECUTED") return "text-green";
  if (o === "BLOCKED" || o === "FAILED") return "text-red";
  if (o === "NO MATCH" || o === "SCHEDULED" || o === "SKIPPED_IDEMPOTENT")
    return "text-amber";
  return "text-ink";
}

export function VerdictPill({ verdict }: { verdict: string }) {
  const tone =
    verdict === "ALLOWED" || verdict === "EXECUTED"
      ? "border-green/40 bg-greenwash text-green"
      : verdict === "HUMAN"
        ? "border-amber/40 bg-amberwash text-amber"
        : verdict === "BLOCKED"
          ? "border-red/40 bg-redwash text-red"
          : "border-line bg-panel2 text-muted";
  const words: Record<string, string> = {
    ALLOWED: "would fire",
    EXECUTED: "fired",
    BLOCKED: "refused",
    HUMAN: "sent to a person",
    SCHEDULED: "waiting for its date",
  };
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold ${tone}`}
    >
      {verdict}
      {words[verdict] && (
        <span className="font-normal opacity-70">· {words[verdict]}</span>
      )}
    </span>
  );
}

function Why({ why }: { why: Record<string, unknown> }) {
  const entries = Object.entries(why).filter(
    ([, v]) => v !== null && v !== undefined && v !== "",
  );
  if (!entries.length) return null;
  return (
    <dl className="mt-2 space-y-1.5 border-t border-line pt-2">
      {entries.map(([k, v]) => (
        <div key={k} className="flex gap-2 text-[11px]">
          <dt className="shrink-0 font-medium text-dim">{k}</dt>
          <dd className="num min-w-0 break-words text-muted">
            {typeof v === "object" ? JSON.stringify(v) : String(v)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function StageCard({ stage }: { stage: TraceStage }) {
  const [open, setOpen] = useState(false);
  const decider = DECIDER[stage.decided_by] ?? DECIDER.runner;

  return (
    <li className="min-w-[228px] flex-1 shrink-0">
      <div className="flex h-full flex-col rounded-2xl border border-line bg-panel p-3">
        <div className="flex items-start justify-between gap-2">
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-dim">
            {stage.stage}
          </p>
          <span
            title={decider.blurb}
            className={`shrink-0 rounded-full border px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide ${decider.className}`}
          >
            <span aria-hidden className="mr-1 opacity-70">
              {decider.glyph}
            </span>
            {decider.label}
          </span>
        </div>

        <p
          className={`num mt-2 text-[15px] font-semibold leading-tight ${outcomeTone(stage.output)}`}
        >
          {stage.output}
        </p>

        {stage.detail && (
          <p className="mt-1.5 text-[11px] leading-relaxed text-muted">
            {stage.detail}
          </p>
        )}

        {!!Object.keys(stage.why ?? {}).length && (
          <>
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="mt-auto cursor-pointer pt-2 text-left text-[10px] font-medium text-dim underline-offset-2 hover:text-ink hover:underline"
            >
              {open ? "hide the evidence" : "why"}
            </button>
            {open && <Why why={stage.why} />}
          </>
        )}
      </div>
    </li>
  );
}

export function TraceStrip({
  stages,
  verdict,
  header,
}: {
  stages: TraceStage[];
  verdict?: string;
  header?: ReactNode;
}) {
  const modelCards = stages.filter((s) => s.decided_by === "model").length;

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3">
        {header}
        {verdict && <VerdictPill verdict={verdict} />}
        <p className="text-[11px] text-dim">
          {modelCards === 0
            ? "The model was never consulted — layer 1 resolved this by lookup."
            : `${modelCards} of ${stages.length} decisions came from the model. It produced a label; it chose no action, amount, time or recipient.`}
        </p>
      </div>

      {/* Horizontal scroll on its own container, never the page body. */}
      <div className="mt-3 overflow-x-auto pb-1">
        <ol className="flex min-w-max items-stretch gap-2">
          {stages.map((stage, i) => (
            <StageCard key={`${stage.stage}-${i}`} stage={stage} />
          ))}
        </ol>
      </div>
    </div>
  );
}
