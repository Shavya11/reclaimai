"use client";

// The promise book, and the conversation that produced it.
//
// This screen shows the one thing a dashboard cannot render as activity: the
// agent deliberately saying nothing. An open promise is a record the system is
// choosing not to chase, and without a place to see them, "the agent went
// quiet" and "the agent forgot" look identical from the outside.
//
// Each row carries the sentence the customer actually wrote. That is the point
// of the whole layer — the label is only trustworthy if you can read what it
// was a label OF, and the Hinglish is what makes it obvious this is not a demo
// running on well-formed English.

import { useCallback, useEffect, useState } from "react";

import { PromiseRow, ReplyRow, fmtTime, get } from "@/lib/api";
import { Badge, Card, Empty, Skeleton, Stat } from "@/components/ui";

const STATE_TONE: Record<string, string> = {
  OPEN: "amber",
  KEPT: "green",
  BROKEN: "red",
};

const STATE_MEANING: Record<string, string> = {
  OPEN: "The agent is deliberately silent until this date.",
  KEPT: "Paid by the date they named. Going quiet is what recovered it.",
  BROKEN: "The date passed unpaid. The record went back to the ladder, one rung further on.",
};

const INTENT_TONE: Record<string, string> = {
  PROMISE_TO_PAY: "green",
  ALREADY_PAID: "blue",
  DISPUTED: "red",
  PARTIAL_PAYMENT_OFFER: "amber",
  WRONG_CONTACT: "violet",
  STOP_CONTACTING: "red",
  UNCLEAR: "plain",
};

export default function Promises({ onOpenRecord }: { onOpenRecord?: (id: string) => void }) {
  const [promises, setPromises] = useState<PromiseRow[] | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [replies, setReplies] = useState<ReplyRow[]>([]);
  const [byIntent, setByIntent] = useState<Record<string, number>>({});

  const load = useCallback(async () => {
    try {
      const [p, r] = await Promise.all([
        get<{ counts: Record<string, number>; promises: PromiseRow[] }>("/api/promises"),
        get<{ by_intent: Record<string, number>; replies: ReplyRow[] }>("/api/replies?limit=120"),
      ]);
      setPromises(p.promises);
      setCounts(p.counts);
      setReplies(r.replies);
      setByIntent(r.by_intent);
    } catch {
      setPromises([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (promises === null) return <Skeleton className="h-64 w-full" />;

  const resolved = (counts.KEPT ?? 0) + (counts.BROKEN ?? 0);
  const keptRate = resolved ? (counts.KEPT ?? 0) / resolved : 0;

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Open promises"
          value={String(counts.OPEN ?? 0)}
          sub="Records the agent is holding contact on, on purpose."
        />
        <Stat label="Kept" value={String(counts.KEPT ?? 0)} tone="green" />
        <Stat label="Broken" value={String(counts.BROKEN ?? 0)} tone="red" />
        <Stat
          label="Kept rate"
          value={resolved ? `${Math.round(keptRate * 100)}%` : "—"}
          sub={
            resolved
              ? "Three in five is the ordinary shape of it — which is why the broken ones have to be caught."
              : "No promise has come due yet."
          }
        />
      </div>

      <Card
        title="The promise book"
        hint="A promise the system cannot break is not a promise, it is a delay. Every one here has a date, and the date is checked."
      >
        {promises.length === 0 ? (
          <Empty>
            No promises yet. Replies are read after a contact is delivered and
            unpaid — run the arc with a model available.
          </Empty>
        ) : (
          <div className="-mx-5 overflow-x-auto">
            <table className="w-full min-w-[760px] text-[13px]">
              <thead>
                <tr className="border-b border-line text-[11px] uppercase tracking-wide text-dim">
                  <th className="px-5 py-2 text-left font-medium">Record</th>
                  <th className="px-2 py-2 text-left font-medium">State</th>
                  <th className="px-2 py-2 text-right font-medium">Amount</th>
                  <th className="px-2 py-2 text-left font-medium">Promised for</th>
                  <th className="px-5 py-2 text-left font-medium">What they said</th>
                </tr>
              </thead>
              <tbody>
                {promises.map((p, i) => (
                  <tr
                    key={`${p.record_id}-${i}`}
                    onClick={() => onOpenRecord?.(p.record_id)}
                    className="cursor-pointer border-b border-line/60 last:border-0 hover:bg-panel2"
                  >
                    <td className="num px-5 py-2.5 text-ink">{p.record_id}</td>
                    <td className="px-2 py-2.5">
                      <Badge tone={STATE_TONE[p.state] ?? "plain"} title={STATE_MEANING[p.state]}>
                        {p.state.toLowerCase()}
                      </Badge>
                    </td>
                    <td className="num px-2 py-2.5 text-right text-muted">{p.amount_display}</td>
                    <td className="num px-2 py-2.5 text-muted">
                      {p.promised_for ? fmtTime(p.promised_for) : "—"}
                    </td>
                    <td className="max-w-[340px] px-5 py-2.5">
                      <p className="truncate text-muted" title={p.reply_text}>
                        {p.reply_text}
                      </p>
                      <p className="num text-[11px] text-dim">
                        read at {Math.round((p.confidence ?? 0) * 100)}% confidence
                      </p>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card
        title="Every reply the agent read"
        hint="The model turns a sentence into one label from a closed set of seven. A deterministic table decides what the label does — five of the seven route to a person."
        right={
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(byIntent)
              .sort((a, b) => b[1] - a[1])
              .map(([intent, n]) => (
                <Badge key={intent} tone={INTENT_TONE[intent] ?? "plain"}>
                  {intent.toLowerCase().replace(/_/g, " ")} {n}
                </Badge>
              ))}
          </div>
        }
      >
        {replies.length === 0 ? (
          <Empty>No replies have arrived yet.</Empty>
        ) : (
          <ul className="space-y-2">
            {replies.map((r, i) => (
              <li
                key={`${r.record_id}-${i}`}
                onClick={() => onOpenRecord?.(r.record_id)}
                className="cursor-pointer rounded-2xl border border-line bg-panel2 px-3 py-2.5 hover:border-green/40"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="num text-[12.5px] font-medium text-ink">
                    {r.record_id}
                  </span>
                  {r.intent && (
                    <Badge tone={INTENT_TONE[r.intent] ?? "plain"}>
                      {r.intent.toLowerCase().replace(/_/g, " ")}
                    </Badge>
                  )}
                  {r.outcome === "PROMISE_REJECTED" && (
                    <Badge tone="amber" title="The model read a date the system refused to act on.">
                      date refused
                    </Badge>
                  )}
                  {r.outcome === "LOW_CONFIDENCE" && (
                    <Badge tone="plain">below the floor</Badge>
                  )}
                  {r.source === "fallback" && (
                    <Badge tone="plain" title="No model available — matched on keywords, deliberately below the confidence floor.">
                      no model
                    </Badge>
                  )}
                  <span className="ml-auto text-[11px] text-dim">
                    {r.at ? fmtTime(r.at) : ""}
                  </span>
                </div>
                <p className="mt-1.5 text-[13px] text-ink">{r.reply_text}</p>
                <p className="mt-0.5 text-[11.5px] leading-snug text-dim">{r.reason}</p>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
