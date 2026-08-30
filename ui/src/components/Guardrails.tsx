"use client";

// The guardrail simulator — all fourteen rules, on a situation you choose.
//
// The passes are shown with the refusals on purpose. A screen that listed only
// blocks would read as a filter with an opinion; showing all fourteen every time
// is what turns "thirteen passed, one refused" into a measurement. So the greens
// are present but quiet, and the one that refused is the only row that speaks.

import { useEffect, useState } from "react";

import { GuardrailRun, Scenario, get, postJSON } from "@/lib/api";
import { Button, Card, Empty, Skeleton } from "@/components/ui";

const BLURB: Record<string, string> = {
  kill_switch: "autopilot off",
  consent: "customer opted out",
  dnd: "on the DND registry",
  quiet_hours: "outside 09:00–20:00 IST",
  max_attempts: "attempt cap reached",
  cooldown: "24h gap not elapsed",
  frequency_cap: "2 contacts / 7 days / customer",
  value_ceiling: "above the ceiling for this leak type",
  daily_budget: "daily action budget",
  idempotency: "this key already executed",
  state_validity: "record no longer actionable",
  confidence_floor: "diagnosis below the floor",
  freshness: "older than 90 days",
  promise_window: "inside a promise they made",
};

type Knobs = {
  hour_ist: number;
  amount_paise: number;
  contacts_last_7d: number;
  attempt_number: number;
  diagnosis_confidence: number;
  opted_out: boolean;
  on_dnd: boolean;
  autopilot_enabled: boolean;
  inside_promise_window: boolean;
  already_executed: boolean;
};

const DEFAULTS: Knobs = {
  hour_ist: 11,
  amount_paise: 250_000,
  contacts_last_7d: 0,
  attempt_number: 1,
  diagnosis_confidence: 0.9,
  opted_out: false,
  on_dnd: false,
  autopilot_enabled: true,
  inside_promise_window: false,
  already_executed: false,
};

function Toggle({
  label,
  hint,
  value,
  onChange,
}: {
  label: string;
  hint: string;
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2 rounded-2xl border border-line bg-panel2 p-2.5">
      <input
        type="checkbox"
        checked={value}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 cursor-pointer"
      />
      <span className="min-w-0">
        <span className="block text-[12px] font-medium text-ink">{label}</span>
        <span className="block text-[10px] leading-snug text-dim">{hint}</span>
      </span>
    </label>
  );
}

export function GuardrailLab() {
  const [scenarios, setScenarios] = useState<Scenario[] | null>(null);
  const [knobs, setKnobs] = useState<Knobs>(DEFAULTS);
  const [run, setRun] = useState<GuardrailRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    get<{ scenarios: Scenario[] }>("/api/sandbox/presets")
      .then((r) => setScenarios(r.scenarios))
      .catch(() => setScenarios([]));
  }, []);

  const evaluate = async (override?: Partial<Knobs>) => {
    const body = { ...knobs, ...(override ?? {}) };
    setBusy(true);
    setError("");
    try {
      setRun(await postJSON<GuardrailRun>("/api/sandbox/guardrails", body));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const applyScenario = (s: Scenario) => {
    const next = { ...DEFAULTS, ...(s.hypothetical as Partial<Knobs>) };
    setKnobs(next);
    void evaluate(next);
  };

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
      <div className="xl:col-span-5">
        <Card title="Describe the moment" hint="then ask all fourteen rules">
          {scenarios === null ? (
            <Skeleton className="h-20" />
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {scenarios.map((s) => (
                <button
                  key={s.label}
                  type="button"
                  title={s.hint}
                  onClick={() => applyScenario(s)}
                  className="cursor-pointer rounded-full border border-line bg-panel px-2.5 py-1.5 text-[11px] text-muted transition-colors hover:border-linestrong hover:text-ink"
                >
                  {s.label}
                </button>
              ))}
            </div>
          )}

          <div className="mt-4 space-y-3">
            <label className="block">
              <span className="flex justify-between text-[11px] font-medium text-dim">
                <span>Hour of day (IST)</span>
                <span className="num text-ink">
                  {String(knobs.hour_ist).padStart(2, "0")}:00
                </span>
              </span>
              <input
                type="range"
                min={0}
                max={23}
                value={knobs.hour_ist}
                onChange={(e) =>
                  setKnobs({ ...knobs, hour_ist: Number(e.target.value) })
                }
                className="mt-1.5 w-full cursor-pointer"
              />
            </label>

            <label className="block">
              <span className="flex justify-between text-[11px] font-medium text-dim">
                <span>Amount</span>
                <span className="num text-ink">
                  ₹{(knobs.amount_paise / 100).toLocaleString("en-IN")}
                </span>
              </span>
              <input
                type="range"
                min={0}
                max={10_000_000}
                step={50_000}
                value={knobs.amount_paise}
                onChange={(e) =>
                  setKnobs({ ...knobs, amount_paise: Number(e.target.value) })
                }
                className="mt-1.5 w-full cursor-pointer"
              />
            </label>

            <label className="block">
              <span className="flex justify-between text-[11px] font-medium text-dim">
                <span>Contacts to this customer, last 7 days</span>
                <span className="num text-ink">{knobs.contacts_last_7d}</span>
              </span>
              <input
                type="range"
                min={0}
                max={5}
                value={knobs.contacts_last_7d}
                onChange={(e) =>
                  setKnobs({
                    ...knobs,
                    contacts_last_7d: Number(e.target.value),
                  })
                }
                className="mt-1.5 w-full cursor-pointer"
              />
            </label>

            <label className="block">
              <span className="flex justify-between text-[11px] font-medium text-dim">
                <span>Diagnosis confidence</span>
                <span className="num text-ink">
                  {knobs.diagnosis_confidence.toFixed(2)}
                </span>
              </span>
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={knobs.diagnosis_confidence}
                onChange={(e) =>
                  setKnobs({
                    ...knobs,
                    diagnosis_confidence: Number(e.target.value),
                  })
                }
                className="mt-1.5 w-full cursor-pointer"
              />
            </label>
          </div>

          <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
            <Toggle
              label="Opted out"
              hint="consent withdrawn — closes the record"
              value={knobs.opted_out}
              onChange={(v) => setKnobs({ ...knobs, opted_out: v })}
            />
            <Toggle
              label="On the DND registry"
              hint="a regulatory list, not a preference"
              value={knobs.on_dnd}
              onChange={(v) => setKnobs({ ...knobs, on_dnd: v })}
            />
            <Toggle
              label="Inside a promise window"
              hint="they named a date; stay quiet until it"
              value={knobs.inside_promise_window}
              onChange={(v) =>
                setKnobs({ ...knobs, inside_promise_window: v })
              }
            />
            <Toggle
              label="This key already executed"
              hint="a replay must never execute twice"
              value={knobs.already_executed}
              onChange={(v) => setKnobs({ ...knobs, already_executed: v })}
            />
            <Toggle
              label="Autopilot on"
              hint="turn it off and everything is refused"
              value={knobs.autopilot_enabled}
              onChange={(v) => setKnobs({ ...knobs, autopilot_enabled: v })}
            />
          </div>

          <Button
            variant="primary"
            className="mt-4"
            busy={busy}
            onClick={() => evaluate()}
          >
            Ask all fourteen
          </Button>

          {error && (
            <p className="mt-3 rounded-2xl border border-red/30 bg-redwash p-3 text-[12px] text-red">
              {error}
            </p>
          )}
        </Card>
      </div>

      <div className="xl:col-span-7">
        <Card
          title="What the gate said"
          hint="fourteen rules, every one of which can refuse"
        >
          {!run ? (
            <Empty>Pick a scenario, or set the dials and ask.</Empty>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <span
                  className={`rounded-full border px-3 py-1.5 text-[12px] font-semibold ${
                    run.allowed
                      ? "border-green/40 bg-greenwash text-green"
                      : "border-red/40 bg-redwash text-red"
                  }`}
                >
                  {run.allowed ? "ALLOWED" : "BLOCKED"}
                </span>
                <p className="num text-[12px] text-muted">
                  {run.passed} passed · {run.blocked} refused
                </p>
                {run.requires_human && (
                  <span className="rounded-full border border-amber/40 bg-amberwash px-2.5 py-1 text-[11px] font-medium text-amber">
                    a person decides this one
                  </span>
                )}
              </div>

              <ul className="mt-4 space-y-1.5">
                {run.rules.map((rule) => {
                  const blocked = rule.verdict === "BLOCK";
                  return (
                    <li
                      key={rule.guardrail}
                      className={`rounded-2xl border p-2.5 ${
                        blocked
                          ? "border-red/30 bg-redwash"
                          : "border-line bg-panel"
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <span
                          aria-hidden
                          className={`num text-[12px] font-bold ${
                            blocked ? "text-red" : "text-green"
                          }`}
                        >
                          {blocked ? "✕" : "✓"}
                        </span>
                        <span
                          className={`num text-[12px] font-medium ${
                            blocked ? "text-red" : "text-muted"
                          }`}
                        >
                          {rule.guardrail}
                        </span>
                        <span className="ml-auto text-[10px] text-dim">
                          {BLURB[rule.guardrail] ?? ""}
                        </span>
                      </div>
                      {blocked && rule.reason && (
                        <p className="mt-1.5 pl-6 text-[11px] leading-relaxed text-ink">
                          {rule.reason}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>

              <p className="num mt-4 break-all rounded-2xl border border-line bg-panel2 p-3 text-[10px] text-dim">
                idempotency key: {run.idempotency_key}
              </p>
            </>
          )}
        </Card>
      </div>
    </div>
  );
}
