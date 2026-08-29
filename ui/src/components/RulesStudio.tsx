"use client";

// The rules studio.
//
// A settings page tells you what a number is. This screen exists to tell you
// what the number COSTS, because that is the only form in which a threshold is
// a decision rather than a preference. Edit a value, press Replay, and the same
// batch runs again under both rules with the difference stated in rupees,
// messages and human escalations — including when the change is worse.
//
// Two things are deliberate in the layout. The replay result sits directly
// under the control that produced it, so cause and effect are one glance apart.
// And a refused edit renders the validator's own list of problems rather than a
// generic error, because "schedule token '20 minutes' is unrecognised" is the
// entire value of refusing it.

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  ReplayDiff,
  RuleChange,
  RulesSnapshot,
  fmtTime,
  get,
  post,
  postJSON,
  validationProblems,
} from "@/lib/api";
import { Badge, Button, Card, Empty, Skeleton } from "@/components/ui";

// Only the thresholds worth putting in front of a merchant. The full config is
// larger, and a screen offering every key equally is a screen nobody reads —
// these are the five somebody actually argues about.
const TUNABLE: Array<{
  section: string;
  field: string;
  label: string;
  hint: string;
  unit?: "paise" | "hours" | "days" | "count" | "ratio";
}> = [
  {
    section: "value_ceiling",
    field: "requires_human_above",
    label: "Value ceiling",
    hint: "Above this, an action needs a person. Bounded authority, in rupees.",
    unit: "paise",
  },
  {
    section: "frequency_cap",
    field: "max_contacts",
    label: "Contacts per customer",
    hint: "Across every record they own, in the window below. The cap teams forget.",
    unit: "count",
  },
  {
    section: "cooldown",
    field: "hours_between_contacts",
    label: "Cooldown",
    hint: "Minimum gap between two messages to one customer.",
    unit: "hours",
  },
  {
    section: "confidence_floor",
    field: "minimum",
    label: "Confidence floor",
    hint: "A diagnosis below this reaches a human instead of moving money.",
    unit: "ratio",
  },
  {
    section: "max_attempts",
    field: "global_hard_cap",
    label: "Attempt hard cap",
    hint: "The ceiling no policy row can raise. Harassment protection.",
    unit: "count",
  },
];

const money = (paise: number) =>
  `₹${Math.round(paise / 100).toLocaleString("en-IN")}`;

function display(value: unknown, unit?: string): string {
  if (typeof value !== "number") return String(value ?? "");
  if (unit === "paise") return String(Math.round(value / 100));
  return String(value);
}

function toStored(raw: string, unit?: string): number {
  const n = Number(raw);
  if (!Number.isFinite(n)) return NaN;
  return unit === "paise" ? Math.round(n * 100) : n;
}

export default function RulesStudio({
  onChanged,
}: {
  onChanged?: () => void;
}) {
  const [snap, setSnap] = useState<RulesSnapshot | null>(null);
  const [changes, setChanges] = useState<RuleChange[]>([]);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [problems, setProblems] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [diff, setDiff] = useState<ReplayDiff | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, c] = await Promise.all([
        get<RulesSnapshot>("/api/admin/rules"),
        get<{ changes: RuleChange[] }>("/api/admin/changes?limit=40"),
      ]);
      setSnap(s);
      setChanges(c.changes);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const current = useMemo(() => {
    const out: Record<string, { value: unknown; modified: boolean }> = {};
    for (const g of snap?.guardrails ?? []) {
      for (const [field, value] of Object.entries(g.config ?? {})) {
        out[`${g.name}.${field}`] = { value, modified: g.modified };
      }
    }
    return out;
  }, [snap]);

  const keyOf = (section: string, field: string) => `${section}.${field}`;

  const edited = useMemo(
    () =>
      TUNABLE.filter((t) => {
        const raw = draft[keyOf(t.section, t.field)];
        if (raw === undefined || raw === "") return false;
        const now = current[keyOf(t.section, t.field)]?.value;
        return toStored(raw, t.unit) !== now;
      }),
    [draft, current],
  );

  const overrides = useMemo(() => {
    const guardrails: Record<string, Record<string, number>> = {};
    for (const t of edited) {
      const value = toStored(draft[keyOf(t.section, t.field)], t.unit);
      if (!Number.isFinite(value)) continue;
      guardrails[t.section] = { ...(guardrails[t.section] ?? {}), [t.field]: value };
    }
    return guardrails;
  }, [edited, draft]);

  const runReplay = async () => {
    setBusy("replay");
    setProblems(null);
    setError(null);
    try {
      setDiff(await postJSON<ReplayDiff>("/api/admin/replay", { guardrails: overrides }));
    } catch (e) {
      const found = validationProblems(e);
      if (found) setProblems(found);
      else setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const applyAll = async () => {
    setBusy("apply");
    setProblems(null);
    setError(null);
    try {
      for (const [section, config] of Object.entries(overrides)) {
        const merged = { ...(snap?.guardrails.find((g) => g.name === section)?.config ?? {}), ...config };
        await postJSON(`/api/admin/guardrail/${section}`, merged);
      }
      setDraft({});
      setDiff(null);
      await load();
      onChanged?.();
    } catch (e) {
      const found = validationProblems(e);
      if (found) setProblems(found);
      else setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const resetAll = async () => {
    setBusy("reset");
    try {
      await post("/api/admin/reset");
      setDraft({});
      setDiff(null);
      await load();
      onChanged?.();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  if (!snap) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded-2xl border border-red/30 bg-redwash px-4 py-3 text-[13px] text-red">
          {error}
        </div>
      )}

      <Card
        title="Guardrail thresholds"
        hint="Every number here is a policy decision somebody should be able to argue with. Change one, then replay the batch to see what it would have cost."
      >
        <div className="space-y-3">
          {TUNABLE.map((t) => {
            const key = keyOf(t.section, t.field);
            const live = current[key];
            const shipped = snap.guardrails.find((g) => g.name === t.section)?.default;
            const shippedValue = shipped?.[t.field];
            const isModified =
              live?.modified && shippedValue !== undefined && shippedValue !== live.value;
            const value = draft[key] ?? display(live?.value, t.unit);
            const changed = edited.some((e) => keyOf(e.section, e.field) === key);

            return (
              <div
                key={key}
                className="flex flex-wrap items-center gap-3 rounded-2xl border border-line bg-panel2 px-3 py-3"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-[13px] font-medium text-ink">{t.label}</span>
                    {isModified && <Badge tone="violet">edited</Badge>}
                    {changed && <Badge tone="amber">unsaved</Badge>}
                  </div>
                  <p className="mt-0.5 text-[11.5px] leading-snug text-dim">{t.hint}</p>
                  {isModified && (
                    <p className="num mt-1 text-[11px] text-dim">
                      shipped default: {display(shippedValue, t.unit)}
                      {t.unit === "paise" ? " (₹)" : ""}
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  {t.unit === "paise" && <span className="text-[13px] text-dim">₹</span>}
                  <input
                    type="number"
                    step={t.unit === "ratio" ? "0.05" : "1"}
                    value={value}
                    onChange={(e) =>
                      setDraft((d) => ({ ...d, [key]: e.target.value }))
                    }
                    className={`num w-32 rounded-xl border bg-panel px-3 py-2 text-right text-[13px] text-ink outline-none transition-colors duration-200 ${
                      changed ? "border-amber" : "border-line focus:border-green"
                    }`}
                  />
                  <span className="w-12 text-[11px] text-dim">
                    {t.unit === "hours" ? "hours" : t.unit === "days" ? "days" : ""}
                  </span>
                </div>
              </div>
            );
          })}
        </div>

        {problems && (
          <div className="mt-3 rounded-2xl border border-red/30 bg-redwash px-4 py-3">
            <p className="text-[12px] font-semibold text-red">
              That rule would not be accepted, so it was not saved:
            </p>
            <ul className="mt-1.5 space-y-1">
              {problems.map((p) => (
                <li key={p} className="text-[12px] leading-snug text-red">
                  · {p}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Button
            variant="primary"
            busy={busy === "replay"}
            disabled={!!busy || edited.length === 0}
            onClick={runReplay}
          >
            Replay this batch
          </Button>
          <Button
            busy={busy === "apply"}
            disabled={!!busy || edited.length === 0}
            onClick={applyAll}
          >
            Apply for real
          </Button>
          <Button busy={busy === "reset"} disabled={!!busy} onClick={resetAll}>
            Reset to defaults
          </Button>
          <span className="ml-auto text-[11.5px] text-dim">
            {edited.length === 0
              ? "Change a threshold to compare."
              : `${edited.length} change${edited.length === 1 ? "" : "s"} pending — replay is free and saves nothing.`}
          </span>
        </div>
      </Card>

      {diff && <ReplayResult diff={diff} />}

      <Card
        title="Change history"
        hint="Append-only, enforced by the database. Who changed what, when, and what it was before."
      >
        {changes.length === 0 ? (
          <Empty>No rule has been changed. Everything is running on the shipped defaults.</Empty>
        ) : (
          <ul className="space-y-2">
            {changes.map((c) => (
              <li
                key={c.id}
                className="rounded-2xl border border-line bg-panel2 px-3 py-2.5"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={c.scope === "guardrail" ? "violet" : "blue"}>
                    {c.scope}
                  </Badge>
                  <span className="num text-[12.5px] font-medium text-ink">
                    {c.key === "*" ? "everything reset to defaults" : c.key}
                  </span>
                  <span className="ml-auto text-[11px] text-dim">
                    {c.changed_at ? fmtTime(c.changed_at) : ""} · {c.actor}
                  </span>
                </div>
                {c.diff.length > 0 && (
                  <div className="mt-1.5 space-y-0.5">
                    {c.diff.map((d) => (
                      <p key={d.field} className="num text-[11.5px] text-muted">
                        {d.field}:{" "}
                        <span className="text-dim line-through">
                          {JSON.stringify(d.before)}
                        </span>{" "}
                        → <span className="text-green">{JSON.stringify(d.after)}</span>
                      </p>
                    ))}
                  </div>
                )}
                {c.note && (
                  <p className="mt-1 text-[11.5px] italic text-dim">{c.note}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

// --- the result ---------------------------------------------------------------

const ROWS: Array<{ key: string; label: string; money?: boolean; good: "up" | "down" }> = [
  { key: "recovered_paise", label: "Recovered", money: true, good: "up" },
  { key: "records_recovered", label: "Records recovered", good: "up" },
  { key: "contacts", label: "Messages sent", good: "down" },
  { key: "escalations", label: "Human escalations", good: "down" },
  { key: "guardrails_total", label: "Guardrail refusals", good: "down" },
];

function ReplayResult({ diff }: { diff: ReplayDiff }) {
  return (
    <Card
      title="What the change would have cost"
      hint="The same 180 records, run twice against a throwaway database. Diagnoses are frozen, so the only thing that differs is the rules — and nothing was saved."
    >
      <div className="rounded-2xl border border-green/30 bg-greenwash px-4 py-3">
        <p className="text-[15px] font-semibold text-ink">{diff.headline}</p>
        <p className="num mt-1 text-[11.5px] text-muted">
          {diff.overrides.join("  ·  ")}
        </p>
      </div>

      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[520px] text-[13px]">
          <thead>
            <tr className="border-b border-line text-[11px] uppercase tracking-wide text-dim">
              <th className="py-2 text-left font-medium">Metric</th>
              <th className="py-2 text-right font-medium">As configured</th>
              <th className="py-2 text-right font-medium">With the change</th>
              <th className="py-2 text-right font-medium">Delta</th>
            </tr>
          </thead>
          <tbody>
            {ROWS.map((r) => {
              const before = diff.baseline[r.key] ?? 0;
              const after = diff.variant[r.key] ?? 0;
              const delta = diff.deltas[r.key] ?? 0;
              const fmt = r.money ? money : (n: number) => n.toLocaleString("en-IN");
              const better =
                delta === 0 ? null : r.good === "up" ? delta > 0 : delta < 0;
              return (
                <tr key={r.key} className="border-b border-line/60 last:border-0">
                  <td className="py-2 text-muted">{r.label}</td>
                  <td className="num py-2 text-right text-muted">{fmt(before)}</td>
                  <td className="num py-2 text-right text-ink">{fmt(after)}</td>
                  <td
                    className={`num py-2 text-right font-medium ${
                      better === null ? "text-dim" : better ? "text-green" : "text-amber"
                    }`}
                  >
                    {delta === 0 ? "—" : `${delta > 0 ? "+" : "−"}${fmt(Math.abs(delta))}`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {diff.guardrails.length > 0 && (
        <div className="mt-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-dim">
            Guardrails that moved
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {diff.guardrails.map((g) => (
              <span
                key={g.guardrail}
                className="num rounded-full border border-line bg-panel2 px-3 py-1.5 text-[11.5px] text-muted"
              >
                {g.guardrail} {g.before} →{" "}
                <span className={g.delta < 0 ? "text-green" : "text-amber"}>
                  {g.after}
                </span>
              </span>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}
