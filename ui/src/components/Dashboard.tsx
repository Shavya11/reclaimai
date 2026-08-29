"use client";

// Screen 1 — the scoreboard.
//
// Four numbers at the top, because a judge should be able to read the outcome
// from across the room. Everything under them exists to make those four
// numbers believable, and everything under them is now a picture rather than a
// column of figures: where the detected money sits, which causes the diagnosis
// actually recovers, what the agent refused to do, and how a naive strategy
// compares on the same batch.
//
// The one rule the layout enforces: the charts show *shape*, and every chart
// carries a "view as table" for the exact numbers. Neither pretends to be the
// other.

import { useEffect, useMemo, useState } from "react";

import { Comparison, Scoreboard, get, pct } from "@/lib/api";
import { Badge, Card, Empty, Skeleton, Stat } from "@/components/ui";
import {
  CauseChart,
  CauseDatum,
  ChartNote,
  ChartTable,
  MoneySplit,
  PairRow,
  PairedBars,
  RailBar,
  ResolutionGauge,
} from "@/components/charts";

const GUARDRAIL_BLURB: Record<string, string> = {
  kill_switch: "autopilot off",
  consent: "customer opted out",
  dnd: "on the DND registry",
  quiet_hours: "outside 09:00–20:00 IST",
  max_attempts: "attempt cap reached",
  cooldown: "24h gap not elapsed",
  frequency_cap: "2 contacts / 7 days / customer",
  value_ceiling: "above ₹50,000 — needs a human",
  daily_budget: "daily action budget",
  idempotency: "already executed",
  state_validity: "record no longer actionable",
  confidence_floor: "diagnosis below 0.6",
  freshness: "older than 90 days",
};

// The registered set is fixed and lives one file per rule in the backend. The
// denominator below is that same list — if a rule is added there without a
// line here, the count is wrong, which is the loudest way to notice.
const REGISTERED = Object.keys(GUARDRAIL_BLURB).length;

const inr = (paise: number) =>
  (paise / 100).toLocaleString("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  });

const inrShort = (paise: number) => {
  const r = paise / 100;
  if (r >= 1e7) return `₹${(r / 1e7).toFixed(2)}Cr`;
  if (r >= 1e5) return `₹${(r / 1e5).toFixed(2)}L`;
  if (r >= 1e3) return `₹${(r / 1e3).toFixed(1)}K`;
  return `₹${r.toFixed(0)}`;
};

export default function Dashboard({
  board,
  onDrill,
}: {
  board: Scoreboard;
  onDrill?: (filter: string) => void;
}) {
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    get<Comparison>("/api/baseline")
      .then((c) => live && setComparison(c))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [board.recovered_paise]);

  const causes: CauseDatum[] = useMemo(
    () =>
      board.by_root_cause
        .filter((c) => c.records > 0)
        .sort((a, b) => b.records - a.records)
        .map((c) => ({
          cause: c.root_cause,
          records: c.records,
          recovered: c.recovered_records,
          rate: c.rate,
          recoveredPaise: c.recovered_paise,
          contacts: c.contacts,
        })),
    [board.by_root_cause],
  );

  // Records held back, not raw refusals. A deferral re-evaluated on twelve
  // ticks is one decision, and quoting 95 where the honest number is 17 is the
  // kind of inflation this whole project is arguing against.
  const held = useMemo(
    () =>
      Object.entries(board.guardrails_fired)
        .map(
          ([name, refusals]) =>
            [name, board.guardrails_records?.[name] ?? refusals, refusals] as const,
        )
        .sort((a, b) => b[1] - a[1]),
    [board.guardrails_fired, board.guardrails_records],
  );
  const heldTotal = held.reduce((n, [, h]) => n + h, 0);
  const maxHeld = Math.max(1, ...held.map(([, h]) => h));

  const pairs: PairRow[] | null = useMemo(() => {
    if (!comparison) return null;
    const b = comparison.baseline;
    return [
      {
        label: "Money recovered",
        naive: b.recovered_paise,
        ours: board.recovered_paise,
        naiveText: inrShort(b.recovered_paise),
        oursText: inrShort(board.recovered_paise),
        lowerIsBetter: false,
        note: "naive collects more",
      },
      {
        label: "Records recovered",
        naive: b.records_recovered,
        ours: board.records_recovered,
        naiveText: String(b.records_recovered),
        oursText: String(board.records_recovered),
        lowerIsBetter: false,
      },
      {
        label: "Customer contacts spent",
        naive: b.contacts,
        ours: board.contacts,
        naiveText: String(b.contacts),
        oursText: String(board.contacts),
        lowerIsBetter: true,
        note: "lower is better",
      },
      {
        label: "Contacts per recovery",
        naive: b.contacts_per_recovery,
        ours: board.contacts_per_recovery,
        naiveText: b.contacts_per_recovery.toFixed(2),
        oursText: board.contacts_per_recovery.toFixed(2),
        lowerIsBetter: true,
      },
    ];
  }, [comparison, board]);

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-12">
      {/* --- the four numbers ------------------------------------------- */}
      <div className="md:col-span-6 xl:col-span-3">
        <Stat
          label="Money at risk"
          value={board.at_risk_display}
          accent
          sub={`${board.records} records \u2014 failed payments, abandoned carts, failed mandates${board.invoice_records ? ` and ${board.invoice_records} overdue invoices` : ""}`}
          onOpen={onDrill && (() => onDrill("all"))}
          openLabel="Open every detected record"
        />
      </div>
      <div className="md:col-span-6 xl:col-span-3">
        <Stat
          label="Recovered"
          value={board.recovered_display}
          tone="green"
          sub={`${pct(board.recovery_rate)} by value · ${board.records_recovered} records · ${board.webhooks_attributed} attributed by webhook`}
          onOpen={onDrill && (() => onDrill("RECOVERED"))}
          openLabel="Open recovered records"
        />
      </div>
      <div className="md:col-span-6 xl:col-span-3">
        <Stat
          label="Still open"
          value={board.open_display}
          tone="amber"
          sub={`${board.records_open} records in flight · ${board.escalations} waiting on a human`}
          onOpen={onDrill && (() => onDrill("AT_RISK"))}
          openLabel="Open at-risk records"
        />
      </div>
      <div className="md:col-span-6 xl:col-span-3">
        <Stat
          label="Written off"
          value={board.unrecoverable_display}
          tone="red"
          sub={`${board.records_unrecoverable} deliberately not chased — never-retry causes and dead mandates`}
          onOpen={onDrill && (() => onDrill("CLOSED"))}
          openLabel="Open stopped records"
        />
      </div>

      {/* --- receivables, when there are any ----------------------------- */}
      {!!board.invoice_records && <Receivables board={board} />}

      {/* --- the money band, tying those four together ------------------- */}
      <div className="md:col-span-12">
        <Card bodyClass="pt-5">
          <MoneySplit
            segments={[
              {
                label: "Recovered",
                paise: board.recovered_paise,
                display: board.recovered_display,
                tone: "green",
              },
              {
                label: "Still open",
                paise: board.open_paise,
                display: board.open_display,
                tone: "amber",
              },
              {
                label: "Written off",
                paise: board.unrecoverable_paise,
                display: board.unrecoverable_display,
                tone: "hatch",
              },
            ]}
          />
          {!board.balances && (
            <p className="mt-3 text-[11px] font-medium text-red">
              Scoreboard does not balance — the three segments should sum to the
              money detected.
            </p>
          )}
        </Card>
      </div>

      {/* --- diagnosis is the product ------------------------------------ */}
      <div className="md:col-span-12 lg:col-span-8">
        <Card
          title="Recovery by root cause"
          hint="Column height is how many records carried that cause; the solid fill is how many came back. Each cause gets a different response, and the rates diverge accordingly."
          className="h-full"
        >
          {causes.length === 0 ? (
            <Empty>Run a batch to populate the scoreboard.</Empty>
          ) : (
            <CauseChart data={causes} />
          )}
        </Card>
      </div>

      <div className="md:col-span-6 lg:col-span-4">
        <Card
          title="How the batch resolved"
          hint="Every detected record ends in exactly one of these three."
          className="h-full"
        >
          <ResolutionGauge
            centre={`${(board.record_recovery_rate * 100).toFixed(1)}%`}
            centreLabel="records recovered"
            segments={[
              { label: "Recovered", value: board.records_recovered, tone: "green" },
              { label: "Open", value: board.records_open, tone: "amber" },
              {
                label: "Written off",
                value: board.records_unrecoverable,
                tone: "hatch",
              },
            ]}
          />
          <ChartNote>
            Recovery rate by <em>record</em>. By value it is{" "}
            {pct(board.recovery_rate)} — the agent recovers proportionally more
            small failures than large ones, because large ones hit the value
            ceiling and go to a human.
          </ChartNote>
        </Card>
      </div>

      {/* --- what it refused to do --------------------------------------- */}
      <div className="md:col-span-6 lg:col-span-4">
        <Card
          title="Guardrails"
          hint={`${heldTotal} records held back. ${held.length} of ${REGISTERED} rules fired on this batch.`}
          className="h-full"
        >
          {held.length === 0 ? (
            <Empty>Nothing has been refused yet.</Empty>
          ) : (
            <>
              <ul className="space-y-3">
                {held.map(([name, records, refusals]) => (
                  <RailBar
                    key={name}
                    label={name}
                    note={`${GUARDRAIL_BLURB[name] ?? "guardrail"} · ${refusals} refusals over the run`}
                    value={records}
                    max={maxHeld}
                    suffix="records"
                  />
                ))}
              </ul>
              <ChartNote>
                Every action the agent wanted to take and was not allowed to.
                The blocks are the point, not the exceptions.
              </ChartNote>
              <ChartTable
                caption="Guardrail refusals by rule"
                head={["Guardrail", "Records held", "Refusals"]}
                rows={held.map(([n, r, f]) => [n, r, f])}
              />
            </>
          )}
        </Card>
      </div>

      {/* --- the comparison ---------------------------------------------- */}
      <div className="md:col-span-6 lg:col-span-5">
        <Card
          title="Versus a naive strategy"
          hint="Retry everything 3× immediately, message every failure. Same records, same seeded outcomes — only the strategy differs."
          className="h-full"
        >
          {failed ? (
            <Empty>Baseline unavailable.</Empty>
          ) : !pairs ? (
            <div className="space-y-3">
              <Skeleton className="h-4 w-1/3" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-2/3" />
            </div>
          ) : (
            <>
              <PairedBars rows={pairs} />
              <ChartNote>
                The naive run collects more rupees. It also spends more than
                twice the customer goodwill per recovery to do it — and the card
                to the right is why it could not ship.
              </ChartNote>
            </>
          )}
        </Card>
      </div>

      {/* --- the punchline ----------------------------------------------- */}
      <div className="md:col-span-6 lg:col-span-3">
        <Card tone="deep" className="h-full">
          <p className="text-[13px] font-medium text-ondeep/80">
            Compliance breaches
          </p>
          <p className="num mt-4 text-[56px] font-bold leading-none tracking-tight on-deep">
            0
          </p>
          <p className="mt-2 text-[11px] leading-relaxed text-ondeep/70">
            Contacts ReclaimAI made that a guardrail forbids. Not a target — a
            structural property of putting the gate above the channel.
          </p>

          {comparison && (
            <div className="mt-5 border-t border-ondeep/15 pt-4">
              <p className="text-[11px] font-medium text-ondeep/80">
                The naive run commits{" "}
                <span className="num font-bold on-deep">
                  {comparison.baseline.compliance_breaches}
                </span>
              </p>
              <ul className="mt-2.5 space-y-1.5">
                {(
                  [
                    ["To opted-out customers", comparison.baseline.contacts_to_opted_out],
                    ["To numbers on DND", comparison.baseline.contacts_to_dnd],
                    ["Inside quiet hours", comparison.baseline.contacts_in_quiet_hours],
                    ["Over the frequency cap", comparison.baseline.customers_over_frequency_cap],
                  ] as Array<[string, number]>
                ).map(([label, n]) => (
                  <li
                    key={label}
                    className="flex items-baseline justify-between gap-3 text-[11px]"
                  >
                    <span className="text-ondeep/70">{label}</span>
                    <span className="num font-semibold on-deep">{n}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-[11px] leading-relaxed text-ondeep/75">
                Plus {comparison.baseline.retries_against_never_retry} retries on
                causes an issuer reads as card testing.
              </p>
            </div>
          )}
        </Card>
      </div>

      {/* --- the honest accounting --------------------------------------- */}
      {comparison && comparison.gap.total.records > 0 && (
        <div className="md:col-span-12 lg:col-span-8">
          <Card
            title="Where the naive strategy wins, and why"
            hint={`It collects ${comparison.gap.total.display} we do not. Every rupee of that is accounted for below.`}
            className="h-full"
          >
            <ul className="space-y-3">
              {comparison.gap.reasons.map((r) => (
                <RailBar
                  key={r.reason}
                  label={r.label}
                  note={`${r.records} record${r.records === 1 ? "" : "s"}`}
                  value={r.paise}
                  valueText={r.display}
                  max={Math.max(
                    1,
                    ...comparison.gap.reasons.map((x) => x.paise),
                  )}
                  tone="amber"
                />
              ))}
            </ul>
            <ChartNote>
              Most of that gap is money the agent was told not to take —
              contacts to people who opted out, amounts above its authority
              ceiling, causes the policy table refuses to retry. Restraint has a
              price and this is it, stated in rupees.
            </ChartNote>
            <ChartTable
              caption="Money the naive strategy collects that ReclaimAI does not"
              head={["Reason", "Records", "Amount"]}
              rows={comparison.gap.reasons.map((r) => [
                r.label,
                r.records,
                r.display,
              ])}
            />
          </Card>
        </div>
      )}

      <div className="md:col-span-12 lg:col-span-4">
        <Card
          title="Cost of a recovery"
          hint="What the agent spent to get there."
          className="h-full"
        >
          <dl className="space-y-2.5 text-[13px]">
            <Row label="Interventions executed" value={board.interventions} />
            <Row label="Customer contacts" value={board.contacts} />
            <Row
              label="Silent retries"
              value={board.silent_retries}
              hint="reached the bank, not the person"
            />
            <Row label="Human escalations" value={board.escalations} />
            <Row
              label="Outcomes attributed"
              value={board.webhooks_attributed}
              hint="via verified webhooks"
            />
          </dl>
          <div className="mt-4 rounded-2xl bg-greenwash p-4">
            <dt className="text-[11px] font-medium text-muted">
              Contacts per recovery
            </dt>
            <dd className="num mt-1 text-[28px] font-bold leading-none text-green">
              {board.contacts_per_recovery.toFixed(2)}
            </dd>
            {comparison && (
              <p className="mt-2 text-[11px] text-muted">
                Naive spends{" "}
                <span className="num font-semibold text-ink">
                  {comparison.baseline.contacts_per_recovery.toFixed(2)}
                </span>{" "}
                for the same job.
              </p>
            )}
          </div>
          <p className="mt-3 text-[11px] text-dim">
            Total money detected this batch: {inr(board.at_risk_paise)}.
          </p>
        </Card>
      </div>

      {failed && (
        <div className="md:col-span-12">
          <Badge tone="amber">
            Baseline comparison unavailable — the rest of the scoreboard is
            unaffected.
          </Badge>
        </div>
      )}
    </div>
  );
}

function Row({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-muted">
        {label}
        {hint && <span className="ml-1.5 text-[11px] text-dim">{hint}</span>}
      </dt>
      <dd className="num shrink-0 font-semibold text-ink">{value}</dd>
    </div>
  );
}


// B2B receivables sit alongside the payments figures rather than in a scoreboard
// of their own. Two scoreboards invite quoting whichever half looks better, and
// "what did the agent recover" is one question.
//
// DSO leads, because it is the number a finance team recognises. A recovery rate
// is our metric; days sales outstanding is theirs, and the whole argument for
// chasing receivables well is made in days rather than percentages.
function Receivables({ board }: { board: Scoreboard }) {
  const improvement = board.dso_improvement ?? 0;
  const promises = board.promises ?? {};
  const resolved = (promises.KEPT ?? 0) + (promises.BROKEN ?? 0);

  return (
    <div className="md:col-span-12">
      <Card
        title="B2B receivables"
        hint="The same engine, a different leak type. An invoice does not fail — it goes unanswered, and the reason is organisational rather than technical."
        right={
          <Badge tone="violet">
            {board.invoice_records} invoices · {board.invoice_at_risk_display}
          </Badge>
        }
      >
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Figure
            label="Days sales outstanding"
            value={`${(board.dso_after ?? 0).toFixed(0)} days`}
            note={
              improvement > 0.05
                ? `${improvement.toFixed(1)} days off the average — value-weighted, so a ₹12 lakh invoice counts for more than a ₹25,000 one`
                : "No movement yet — DSO improves as invoices settle earlier in the arc"
            }
            tone={improvement > 0.05 ? "green" : "ink"}
          />
          <Figure
            label="Recovered from invoices"
            value={board.invoice_recovered_display ?? "₹0"}
            note={`${pct(board.invoice_recovery_rate ?? 0)} by value · ${board.invoice_recovered_records ?? 0} invoices`}
            tone="green"
          />
          <Figure
            label="Promises to pay"
            value={String(promises.OPEN ?? 0)}
            note={
              resolved
                ? `${promises.KEPT ?? 0} kept, ${promises.BROKEN ?? 0} broken — a broken one climbs the ladder a rung`
                : "Open promises are records the agent is holding contact on, on purpose"
            }
            tone="amber"
          />
          <Figure
            label="Replies read"
            value={String(board.replies_read ?? 0)}
            note="Each one labelled from a closed set of seven intents. Five of the seven route to a person."
          />
        </div>
      </Card>
    </div>
  );
}

function Figure({
  label,
  value,
  note,
  tone = "ink",
}: {
  label: string;
  value: string;
  note: string;
  tone?: "ink" | "green" | "amber";
}) {
  const colour =
    tone === "green" ? "text-green" : tone === "amber" ? "text-amber" : "text-ink";
  return (
    <div className="rounded-2xl border border-line bg-panel2 p-4">
      <p className="text-[12px] font-medium text-muted">{label}</p>
      <p className={`num mt-2 text-[24px] font-bold leading-none tracking-tight ${colour}`}>
        {value}
      </p>
      <p className="mt-2 text-[11px] leading-relaxed text-dim">{note}</p>
    </div>
  );
}
