"use client";

// The Try-it tab — hand the agent something and watch it decide.
//
// Every other screen shows the settled result of a batch that already ran. This
// one is the only place a visitor can put something IN, which makes it the only
// place the pipeline can be checked rather than read about.
//
// Two buttons, and the difference between them is the whole design. **Preview**
// runs the real diagnosers, the real policy table and the real gate and writes
// nothing at all — a `verify` check counts the rows either side of five of them
// to prove it. **Commit** persists the record and hands it to the same runner
// every seeded record goes through, so it lands on the dashboard, in the queue
// and in the audit trail. Committed records are counted apart from every
// published figure; the card on the dashboard says so and explains why.

import { useCallback, useEffect, useState } from "react";

import { Preset, Submission, Trace, get, postJSON, post } from "@/lib/api";
import { Badge, Button, Card, Empty, Skeleton } from "@/components/ui";
import { TraceStrip } from "@/components/Trace";
import { ReplyLab } from "@/components/ReplyLab";
import { GuardrailLab } from "@/components/Guardrails";

const RUPEE = "₹";

function rupees(paise: number): string {
  return `${RUPEE}${(paise / 100).toLocaleString("en-IN", {
    maximumFractionDigits: 0,
  })}`;
}

type Mode = "classify" | "reply" | "guardrails";

const MODES: Array<[Mode, string, string]> = [
  ["classify", "Classify a failure", "diagnose it, decide, and gate it"],
  ["reply", "Read a reply", "and watch the agent go quiet"],
  ["guardrails", "Point the rules at something", "all fourteen, pass or refuse"],
];

export function TryIt({ onCommitted }: { onCommitted?: () => void }) {
  const [mode, setMode] = useState<Mode>("classify");

  return (
    <div>
      <div
        role="tablist"
        aria-label="Sandbox mode"
        className="mb-4 flex flex-wrap gap-1.5"
      >
        {MODES.map(([key, label, hint]) => (
          <button
            key={key}
            role="tab"
            type="button"
            title={hint}
            aria-selected={mode === key}
            onClick={() => setMode(key)}
            className={`cursor-pointer rounded-full border px-3.5 py-2 text-[12px] font-medium transition-colors ${
              mode === key
                ? "border-green/40 bg-greenwash text-green"
                : "border-line bg-panel text-muted hover:border-linestrong hover:text-ink"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {mode === "classify" ? (
        <Classify onCommitted={onCommitted} />
      ) : mode === "reply" ? (
        <ReplyLab />
      ) : (
        <GuardrailLab />
      )}
    </div>
  );
}

function Classify({ onCommitted }: { onCommitted?: () => void }) {
  const [presets, setPresets] = useState<Preset[] | null>(null);
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  const [amount, setAmount] = useState(250_000);
  const [withoutModel, setWithoutModel] = useState(false);

  const [trace, setTrace] = useState<Trace | null>(null);
  const [busy, setBusy] = useState<"" | "preview" | "commit" | "reset">("");
  const [error, setError] = useState("");

  useEffect(() => {
    get<{ presets: Preset[] }>("/api/sandbox/presets")
      .then((r) => setPresets(r.presets))
      .catch(() => setPresets([]));
  }, []);

  const submission = useCallback(
    (): Submission => ({
      text,
      error_reason: reason,
      amount_paise: amount,
      without_model: withoutModel,
    }),
    [text, reason, amount, withoutModel],
  );

  const run = async (mode: "preview" | "commit") => {
    setBusy(mode);
    setError("");
    try {
      const result = await postJSON<Trace>(
        `/api/sandbox/${mode}`,
        submission(),
      );
      setTrace(result);
      if (mode === "commit") onCommitted?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  };

  const reset = async () => {
    setBusy("reset");
    setError("");
    try {
      await post("/api/sandbox/reset");
      setTrace(null);
      onCommitted?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  };

  const applyPreset = (preset: Preset) => {
    const s = preset.submission as Submission;
    setText(String(s.text ?? ""));
    setReason(String(s.error_reason ?? ""));
    setAmount(Number(s.amount_paise ?? 250_000));
    setTrace(null);
  };

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
      {/* --- the input ---------------------------------------------------- */}
      <div className="xl:col-span-5">
        <Card
          title="Describe a failed payment"
          hint="the agent has never seen this one"
        >
          {presets === null ? (
            <Skeleton className="h-24" />
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {presets.map((p) => (
                <button
                  key={p.label}
                  type="button"
                  title={p.hint}
                  onClick={() => applyPreset(p)}
                  className="cursor-pointer rounded-full border border-line bg-panel px-2.5 py-1.5 text-[11px] text-muted transition-colors hover:border-linestrong hover:text-ink"
                >
                  {p.label}
                </button>
              ))}
            </div>
          )}

          <label className="mt-4 block">
            <span className="text-[11px] font-medium text-dim">
              What happened, in your own words
            </span>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={3}
              placeholder="Customer says the payment failed twice last night."
              className="mt-1.5 w-full rounded-2xl border border-line bg-panel2 p-3 text-[13px] text-ink outline-none focus:border-linestrong"
            />
          </label>

          <div className="mt-3 grid grid-cols-2 gap-3">
            <label className="block">
              <span className="text-[11px] font-medium text-dim">
                Razorpay error reason
              </span>
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="card_expired"
                className="num mt-1.5 w-full rounded-2xl border border-line bg-panel2 px-3 py-2 text-[13px] text-ink outline-none focus:border-linestrong"
              />
              <span className="mt-1 block text-[10px] leading-snug text-dim">
                Leave it empty and layer 1 has nothing to look up, so the model
                is asked instead.
              </span>
            </label>
            <label className="block">
              <span className="text-[11px] font-medium text-dim">
                Amount ({RUPEE})
              </span>
              <input
                type="number"
                min={0}
                value={Math.round(amount / 100)}
                onChange={(e) =>
                  setAmount(Math.max(0, Number(e.target.value) * 100))
                }
                className="num mt-1.5 w-full rounded-2xl border border-line bg-panel2 px-3 py-2 text-[13px] text-ink outline-none focus:border-linestrong"
              />
              <span className="mt-1 block text-[10px] leading-snug text-dim">
                Above the ceiling for its leak type, no action fires and a person
                is asked.
              </span>
            </label>
          </div>

          <label className="mt-3 flex cursor-pointer items-start gap-2 rounded-2xl border border-line bg-panel2 p-3">
            <input
              type="checkbox"
              checked={withoutModel}
              onChange={(e) => setWithoutModel(e.target.checked)}
              className="mt-0.5 cursor-pointer"
            />
            <span>
              <span className="text-[12px] font-medium text-ink">
                Pretend the model is down
              </span>
              <span className="mt-0.5 block text-[10px] leading-snug text-dim">
                Layer 2 refuses to answer. The chain has to reach UNKNOWN and a
                person rather than guess — the batch always completes.
              </span>
            </span>
          </label>

          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              variant="secondary"
              busy={busy === "preview"}
              disabled={!!busy}
              onClick={() => run("preview")}
            >
              Preview
            </Button>
            <Button
              variant="primary"
              busy={busy === "commit"}
              disabled={!!busy}
              onClick={() => run("commit")}
            >
              Commit as a real record
            </Button>
            <Button
              variant="ghost"
              busy={busy === "reset"}
              disabled={!!busy}
              onClick={reset}
              className="ml-auto"
            >
              Reset the demo
            </Button>
          </div>

          <p className="mt-3 text-[11px] leading-relaxed text-dim">
            <strong className="font-semibold text-muted">Preview</strong> writes
            nothing — not a record, not an audit row, not a clock tick.{" "}
            <strong className="font-semibold text-muted">Commit</strong> runs the
            same runner every other record goes through, and the result shows up
            on the dashboard, the recovery queue and the audit trail. Committed
            records are counted apart from the published figures.
          </p>

          {error && (
            <p className="mt-3 rounded-2xl border border-red/30 bg-redwash p-3 text-[12px] text-red">
              {error}
            </p>
          )}
        </Card>
      </div>

      {/* --- the trace ---------------------------------------------------- */}
      <div className="xl:col-span-7">
        <Card
          title="What the agent did with it"
          hint={
            trace?.committed
              ? "read back off the audit log, not reported from memory"
              : "every stage, and who decided it"
          }
        >
          {!trace ? (
            <Empty>
              Pick an example or describe a failure, then press Preview.
            </Empty>
          ) : (
            <>
              <TraceStrip
                stages={trace.trace}
                verdict={trace.verdict}
                header={
                  <Badge tone={trace.committed ? "green" : "plain"}>
                    {trace.committed
                      ? `committed as ${trace.record_id}`
                      : "preview — nothing was written"}
                  </Badge>
                }
              />
              {trace.committed && (
                <p className="mt-4 rounded-2xl border border-line bg-panel2 p-3 text-[12px] leading-relaxed text-muted">
                  <strong className="font-semibold text-ink">
                    {trace.record_id}
                  </strong>{" "}
                  is now a record like any other: {rupees(amount)} at risk, on
                  the dashboard under &ldquo;Submitted from the dashboard&rdquo;,
                  in the recovery queue, and with its own audit trail. It obeys
                  all fourteen guardrails, including the seven-day frequency cap
                  it shares with the seeded batch.
                </p>
              )}
            </>
          )}
        </Card>
      </div>
    </div>
  );
}
