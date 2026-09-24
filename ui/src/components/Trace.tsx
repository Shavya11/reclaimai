"use client";

// The trace — one row per stage, top to bottom, on a single spine.
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

function StageRow({
  stage,
  index,
  last,
}: {
  stage: TraceStage;
  index: number;
  last: boolean;
}) {
  const [open, setOpen] = useState(false);
  const decider = DECIDER[stage.decided_by] ?? DECIDER.runner;
  const isModel = stage.decided_by === "model";
  const hasWhy = !!Object.keys(stage.why ?? {}).length;

  return (
    <li className="relative flex gap-3">
      {/* The spine: a node per stage, joined top to bottom, so the order reads
          without a horizontal scroll that hides the last two stages. */}
      <div className="flex w-7 shrink-0 flex-col items-center">
        <span
          className={`num flex h-7 w-7 items-center justify-center rounded-full border text-[11px] font-bold ${decider.className}`}
          title={decider.blurb}
        >
          {index + 1}
        </span>
        {!last && <span aria-hidden className="w-px flex-1 bg-line" />}
      </div>

      <div
        className={`mb-2 min-w-0 flex-1 rounded-2xl border px-3.5 py-2.5 ${
          isModel ? "border-amber/40 bg-amberwash/60" : "border-line bg-panel"
        }`}
      >
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-dim">
            {stage.stage}
          </p>
          <p
            className={`num text-[14px] font-semibold leading-tight ${outcomeTone(stage.output)}`}
          >
            {stage.output}
          </p>
          <span
            title={decider.blurb}
            className={`ml-auto shrink-0 rounded-full border px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide ${decider.className}`}
          >
            <span aria-hidden className="mr-1 opacity-70">
              {decider.glyph}
            </span>
            {decider.label}
          </span>
        </div>

        {stage.detail && (
          <p className="mt-1 text-[12px] leading-relaxed text-muted">
            {stage.detail}
          </p>
        )}

        {hasWhy && (
          <>
            <button
              type="button"
              aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
              className="mt-1 cursor-pointer text-[10px] font-medium text-dim underline-offset-2 hover:text-ink hover:underline"
            >
              {open ? "hide the evidence" : "show the evidence"}
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
  const tally = (Object.keys(DECIDER) as TraceStage["decided_by"][])
    .map((k) => [k, stages.filter((s) => s.decided_by === k).length] as const)
    .filter(([, n]) => n > 0);
  // Two different flows share this component, and "no model call" means a
  // different thing in each: a diagnosis trace can resolve at layer 1 (a real
  // lookup table keyed on the error reason), while a reply trace has no layer
  // 1 at all — its only fallback is a fixed keyword match, which is a weaker
  // claim and needs saying as one. Told apart by the first stage's name rather
  // than a prop, so a caller cannot forget to pass one.
  const isReply = stages[0]?.stage === "REPLY";

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        {header}
        {verdict && <VerdictPill verdict={verdict} />}
      </div>

      <div className="mt-3 rounded-2xl border border-line bg-panel2 px-3.5 py-2.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-dim">
            Who decided
          </span>
          {tally.map(([k, n]) => (
            <span
              key={k}
              title={DECIDER[k].blurb}
              className={`num rounded-full border px-2 py-0.5 text-[10px] font-semibold ${DECIDER[k].className}`}
            >
              {DECIDER[k].label} × {n}
            </span>
          ))}
        </div>
        <p className="mt-1.5 text-[11px] leading-relaxed text-dim">
          {modelCards === 0
            ? isReply
              ? "No model was reachable for this reply — a fixed keyword match stood in, well below the confidence floor, so it labels the reply for a person rather than pretending to have understood it."
              : "The model was never consulted — layer 1 resolved this by lookup."
            : `${modelCards} of ${stages.length} decisions came from the model. It produced a label; it chose no action, amount, time or recipient.`}
        </p>
      </div>

      <ol className="mt-3">
        {stages.map((stage, i) => (
          <StageRow
            key={`${stage.stage}-${i}`}
            stage={stage}
            index={i}
            last={i === stages.length - 1}
          />
        ))}
      </ol>
    </div>
  );
}
