"use client";

// Reply mode — type what a customer wrote and watch the agent decide to say
// nothing.
//
// This is the hardest thing in the product to demonstrate, because a promise
// the system accepts produces silence, and silence looks identical to a system
// that is broken. So the last card says it in words: the agent goes quiet until
// a date, guardrail 14 is what enforces that, and the date the model read had to
// clear a deterministic validator before any of it counted.
//
// Three refusals live between the model and any effect, and each gets its own
// card because each refuses something different: the confidence floor, the date
// validator, and the effects table.

import { useEffect, useState } from "react";

import { ReplyPreset, Trace, get, postJSON } from "@/lib/api";
import { Card, Empty, Button, Skeleton } from "@/components/ui";
import { TraceStrip } from "@/components/Trace";

export function ReplyLab() {
  const [presets, setPresets] = useState<ReplyPreset[] | null>(null);
  const [text, setText] = useState("");
  const [withoutModel, setWithoutModel] = useState(false);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    get<{ replies: ReplyPreset[] }>("/api/sandbox/presets")
      .then((r) => setPresets(r.replies))
      .catch(() => setPresets([]));
  }, []);

  const read = async (override?: string) => {
    const body = { text: override ?? text, without_model: withoutModel };
    setBusy(true);
    setError("");
    try {
      setTrace(await postJSON<Trace>("/api/sandbox/reply", body));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
      <div className="xl:col-span-5">
        <Card
          title="What did the customer write back?"
          hint="Hinglish included — that is how these actually arrive"
        >
          {presets === null ? (
            <Skeleton className="h-20" />
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {presets.map((p) => (
                <button
                  key={p.label}
                  type="button"
                  title={p.hint}
                  onClick={() => {
                    setText(p.text);
                    void read(p.text);
                  }}
                  className="cursor-pointer rounded-full border border-line bg-panel px-2.5 py-1.5 text-[11px] text-muted transition-colors hover:border-linestrong hover:text-ink"
                >
                  {p.label}
                </button>
              ))}
            </div>
          )}

          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={4}
            placeholder="paisa 5 tareek ko bhej dunga bhai"
            className="mt-4 w-full rounded-2xl border border-line bg-panel2 p-3 text-[13px] text-ink outline-none focus:border-linestrong"
          />

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
                A substring match takes over at confidence 0.50 — deliberately
                below the floor, so it labels the reply for a person rather than
                pretending to have understood it.
              </span>
            </span>
          </label>

          <Button
            variant="primary"
            className="mt-4"
            busy={busy}
            disabled={!text.trim()}
            onClick={() => read()}
          >
            Read it
          </Button>

          <p className="mt-3 text-[11px] leading-relaxed text-dim">
            Nothing is written. The promise book, the audit log and the records
            are untouched — this shows what the reading would do, not what it
            did.
          </p>

          {error && (
            <p className="mt-3 rounded-2xl border border-red/30 bg-redwash p-3 text-[12px] text-red">
              {error}
            </p>
          )}
        </Card>
      </div>

      <div className="xl:col-span-7">
        <Card
          title="How it was read, and what that does"
          hint="the model labels; three deterministic gates decide"
        >
          {!trace ? (
            <Empty>Pick a reply, or type one, then press Read it.</Empty>
          ) : (
            <>
              <TraceStrip stages={trace.trace} verdict={trace.verdict} />
              {trace.verdict === "PROMISED" && (
                <p className="mt-4 rounded-2xl border border-green/30 bg-greenwash p-3 text-[12px] leading-relaxed text-ink">
                  <strong className="font-semibold">
                    The agent now says nothing.
                  </strong>{" "}
                  That is the outcome, and it is the one a dashboard cannot draw:
                  no message, no retry, no ladder rung, until the date passes.
                  Guardrail 14 refuses every contact in the meantime, and when
                  the date arrives the promise is checked rather than assumed —
                  kept, or broken and one rung further on.
                </p>
              )}
              {trace.verdict === "OPTED_OUT" && (
                <p className="mt-4 rounded-2xl border border-line bg-panel2 p-3 text-[12px] leading-relaxed text-muted">
                  Consent is not a probability. A request to stop is honoured at
                  any confidence, because the cost of wrongly staying silent is
                  one unsent message and the cost of wrongly continuing is a
                  compliance breach.
                </p>
              )}
            </>
          )}
        </Card>
      </div>
    </div>
  );
}
