"use client";

// The shell: four screens, one page, and the controls the demo is driven from.
//
// One page rather than four routes, deliberately. A static export with client
// routing means no navigation on stage, no reload between beats, and nothing
// that can 404 in front of an audience.
//
// Layout is a fixed rail plus a scrolling column. The rail holds *where you
// are* and *what state the system is in*; the header holds *what you can do to
// it*. Keeping those apart is why the demo controls never get lost among the
// numbers.

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import {
  Health,
  QueueItem,
  RecordRow,
  Scoreboard,
  fmtTime,
  get,
  isBusy,
  post,
} from "@/lib/api";
import { Button, Card, Empty, Skeleton } from "@/components/ui";
import {
  IconAlert,
  IconAudit,
  IconClock,
  IconDashboard,
  IconHuman,
  IconMoon,
  IconPlay,
  IconPower,
  IconPromise,
  IconQueue,
  IconRules,
  IconSearch,
  IconSun,
} from "@/components/icons";
import Dashboard from "@/components/Dashboard";
import Queue from "@/components/Queue";
import AuditTrail from "@/components/AuditTrail";
import Promises from "@/components/Promises";
import RulesStudio from "@/components/RulesStudio";

type Tab = "dashboard" | "queue" | "promises" | "human" | "rules" | "audit";

const TICKS: Array<[string, string]> = [
  ["20m", "20m"],
  ["2h", "2h"],
  ["24h", "24h"],
  ["48h", "48h"],
  ["next_salary_window", "1st"],
  ["+7d", "+7d"],
];

// Long enough to cover a full arc walked live (~100s at a 3s poll) plus a slow
// cold start, and short enough that a page left open overnight stops knocking.
const MAX_POLLS = 140;

const PAGE_TITLE: Record<Tab, [string, string]> = {
  dashboard: [
    "Dashboard",
    "The model produces a label. A policy table decides the action. A guardrail engine decides whether it may fire.",
  ],
  queue: [
    "Recovery queue",
    "Every at-risk record, what was diagnosed, what happens next — and why the ones sitting still are sitting still.",
  ],
  human: [
    "Human queue",
    "Five policy rows are no_auto_action by design. Knowing when to stop and fetch a person is the differentiator, not a gap.",
  ],
  promises: [
    "Promises & replies",
    "The agent deliberately saying nothing. A customer named a date, so contact is held until it passes \u2014 and checked when it does.",
  ],
  rules: [
    "Rules studio",
    "Every threshold is a policy decision somebody should be able to argue with. Change one and replay the same batch to see what it would have cost.",
  ],
  audit: [
    "Audit trail",
    "Append-only. Every row was written at the moment the decision was taken, including the ones that refused.",
  ],
};

export default function Page() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [board, setBoard] = useState<Scoreboard | null>(null);
  const [records, setRecords] = useState<RecordRow[]>([]);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [openRecord, setOpenRecord] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [queueFilter, setQueueFilter] = useState("all");
  // A cold instance answers before it has anything to answer with, and an empty
  // database renders as a settled row of zeroes. This used to be inferred from
  // the shape of the scoreboard, and the inference had a hole exactly where it
  // mattered: during the first seconds of a boot `records` is 0, which the test
  // read as "nothing to wait for" — so it hid the banner, stopped polling, and
  // left ₹0 on screen as though the agent had recovered nothing. The API now
  // reports it outright.
  const [seeding, setSeeding] = useState(false);
  // null = we have not heard back yet. Distinguishing that from `false` is the
  // whole point: an API that never answered is not an API that said no.
  const [connected, setConnected] = useState<boolean | null>(null);
  // Bumped after every attempt, successful or not, so the poll effect below
  // re-arms itself. Without it a failed request breaks the retry chain — which
  // is how one slow cold start became a page stuck on skeletons forever.
  const [beat, setBeat] = useState(0);
  const failures = useRef(0);
  const polls = useRef(0);

  const refresh = useCallback(async () => {
    try {
      const [b, r, q, h] = await Promise.all([
        get<Scoreboard>("/api/scoreboard"),
        get<{ records: RecordRow[] }>("/api/records?limit=500"),
        get<{ items: QueueItem[] }>("/api/human-queue"),
        get<Health>("/api/health"),
      ]);
      setBoard(b);
      setRecords(r.records);
      setQueue(q.items);
      setHealth(h);
      setError(null);
      setConnected(true);
      failures.current = 0;
      // The heuristic survives only as a fallback for an API deployed before
      // /api/health grew the flag. It is wrong in the first seconds of a boot,
      // which is why it is no longer the primary answer.
      setSeeding(h.seeding ?? (b.records > 0 && b.interventions === 0));
    } catch (e) {
      failures.current += 1;
      setError(String(e));
      setConnected(false);
      setSeeding(false);
    } finally {
      setBeat((n) => n + 1);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // One timer, one rule: keep asking while there is a reason to. There are two
  // reasons — we have not had a good answer yet, or the API says it is still
  // building the batch — and both end on their own.
  //
  // A cold free instance can take most of a minute just to wake, so giving up
  // after the first failure is giving up on an API that was about to answer.
  // It backs off when nothing is home and stays brisk while a batch is landing.
  useEffect(() => {
    const waiting = connected !== true || seeding;
    if (!waiting) {
      polls.current = 0;
      return;
    }
    if (polls.current > MAX_POLLS) return;
    const delay =
      connected === true
        ? 3000
        : Math.min(3000 * 2 ** Math.min(failures.current, 3), 24000);
    const id = setTimeout(() => {
      polls.current += 1;
      refresh();
    }, delay);
    return () => clearTimeout(id);
  }, [beat, connected, seeding, refresh]);

  const act = async (label: string, path: string) => {
    setBusy(label);
    try {
      await post(path);
      // The API accepted the work; it has not done it. Raise the banner now
      // rather than waiting for a poll to notice, so a button that started a
      // two-minute arc does not look like a button that did nothing.
      setSeeding(true);
      polls.current = 0;
      setError(null);
      await refresh();
    } catch (e) {
      // A refusal because something else is already running is information,
      // not a failure worth a red banner.
      if (isBusy(e)) {
        setSeeding(true);
        polls.current = 0;
      } else {
        setError(String(e));
      }
    } finally {
      setBusy(null);
    }
  };

  const openAudit = (id: string) => {
    setOpenRecord(id);
    setTab("audit");
  };

  const drill = (filter: string) => {
    setQueueFilter(filter);
    setSearch("");
    setTab("queue");
  };

  const [title, blurb] = PAGE_TITLE[tab];
  const openPromises = board?.promises?.OPEN ?? 0;
  const nav: Array<[Tab, string, typeof IconDashboard, number | null]> = [
    ["dashboard", "Dashboard", IconDashboard, null],
    ["queue", "Recovery queue", IconQueue, records.length || null],
    ["promises", "Promises & replies", IconPromise, openPromises || null],
    ["human", "Human queue", IconHuman, queue.length || null],
    ["rules", "Rules studio", IconRules, null],
    ["audit", "Audit trail", IconAudit, null],
  ];

  return (
    <div className="mx-auto flex min-h-screen max-w-[1560px] gap-4 p-3 lg:p-4">
      {/* --- the rail ------------------------------------------------------ */}
      <aside className="sticky top-4 hidden h-[calc(100vh-2rem)] w-[244px] shrink-0 flex-col rounded-3xl border border-line bg-panel p-4 shadow-[var(--shadow-card)] lg:flex">
        <Wordmark />

        <nav aria-label="Primary" className="mt-7">
          <p className="px-3 text-[10px] font-semibold uppercase tracking-[0.14em] text-dim">
            Menu
          </p>
          <ul className="mt-2 space-y-0.5">
            {nav.map(([key, label, Icon, count]) => (
              <li key={key}>
                <button
                  type="button"
                  onClick={() => setTab(key)}
                  aria-current={tab === key ? "page" : undefined}
                  className={`flex w-full cursor-pointer items-center gap-2.5 rounded-2xl px-3 py-2.5 text-[13px] font-medium transition-colors duration-200 ${
                    tab === key
                      ? "bg-greenwash text-green"
                      : "text-muted hover:bg-panel2 hover:text-ink"
                  }`}
                >
                  <Icon className="h-[18px] w-[18px] shrink-0" />
                  <span className="truncate">{label}</span>
                  {count !== null && (
                    <span className="num ml-auto shrink-0 rounded-full bg-panel2 px-1.5 py-0.5 text-[10px] text-dim">
                      {count}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <div className="mt-6">
          <p className="px-3 text-[10px] font-semibold uppercase tracking-[0.14em] text-dim">
            System
          </p>
          <div className="mt-2 space-y-2 px-1">
            <button
              type="button"
              onClick={() =>
                act(
                  "kill",
                  `/api/kill-switch?enabled=${health?.autopilot_enabled ? "false" : "true"}`,
                )
              }
              disabled={!!busy || seeding || !health}
              title="Guardrail #1 — blocks every action while off"
              className={`flex w-full cursor-pointer items-center gap-2.5 rounded-2xl border px-3 py-2.5 text-[13px] font-medium transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-50 ${
                !health || health.autopilot_enabled
                  ? "border-red/30 bg-redwash text-red hover:border-red/60"
                  : "border-green/30 bg-greenwash text-green hover:border-green/60"
              }`}
            >
              <IconPower className="h-[18px] w-[18px] shrink-0" />
              {!health || health.autopilot_enabled ? "Kill switch" : "Re-arm agent"}
            </button>
            <ThemeToggle />
          </div>
        </div>

        <div className="mt-auto pt-4">
          <SystemCard health={health} connected={connected} />
        </div>
      </aside>

      {/* --- the column ---------------------------------------------------- */}
      <div className="flex min-w-0 flex-1 flex-col gap-4">
        <header className="flex items-center gap-3 rounded-3xl border border-line bg-panel px-3 py-3 shadow-[var(--shadow-card)] sm:px-4">
          <div className="lg:hidden">
            <Wordmark compact />
          </div>

          <label className="relative min-w-0 flex-1 sm:max-w-md">
            <span className="sr-only">Search records, customers or causes</span>
            <IconSearch className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-dim" />
            <input
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                if (e.target.value && tab !== "queue") setTab("queue");
              }}
              placeholder="Search record, customer, cause…"
              className="w-full rounded-full border border-line bg-panel2 py-2.5 pl-9 pr-3 text-[13px] text-ink outline-none transition-colors duration-200 placeholder:text-dim focus:border-green"
            />
          </label>

          <div className="ml-auto flex shrink-0 items-center gap-2">
            {health && (
              <span className="num hidden items-center gap-1.5 rounded-full border border-line px-3 py-2 text-[11px] text-muted xl:inline-flex">
                <IconClock className="h-3.5 w-3.5 text-dim" />
                {fmtTime(health.clock)}
                {health.time_travelled && (
                  <span className="text-violet">· travelled</span>
                )}
              </span>
            )}
            <Button
              variant="primary"
              icon={<IconPlay className="h-3.5 w-3.5" />}
              busy={busy === "run" || seeding}
              onClick={() => act("run", "/api/run-batch")}
              disabled={!!busy || seeding}
              title="Reset and walk the whole demo arc from t=0. Takes about two minutes."
            >
              {busy === "run" || seeding ? "Running…" : "Run batch"}
            </Button>
          </div>
        </header>

        {/* Mobile nav — a scrollable pill row rather than a drawer. Four
            destinations do not justify a focus trap. */}
        <nav aria-label="Primary" className="lg:hidden">
          <ul className="flex gap-1.5 overflow-x-auto pb-1">
            {nav.map(([key, label, Icon, count]) => (
              <li key={key} className="shrink-0">
                <button
                  type="button"
                  onClick={() => setTab(key)}
                  aria-current={tab === key ? "page" : undefined}
                  className={`flex cursor-pointer items-center gap-2 rounded-full border px-3.5 py-2.5 text-[13px] font-medium transition-colors duration-200 ${
                    tab === key
                      ? "border-green/30 bg-greenwash text-green"
                      : "border-line bg-panel text-muted"
                  }`}
                >
                  <Icon className="h-4 w-4" />
                  {label}
                  {count !== null && <span className="num text-dim">{count}</span>}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3 px-1">
          <div className="min-w-0">
            <h1 className="text-[26px] font-bold tracking-tight text-ink sm:text-[30px]">
              {title}
            </h1>
            <p className="mt-1 max-w-2xl text-[12px] leading-relaxed text-muted">
              {blurb}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-0.5 text-[11px] font-medium text-dim">
              Advance clock
            </span>
            {TICKS.map(([value, label]) => (
              <Button
                key={value}
                variant="chip"
                busy={busy === value}
                disabled={!!busy || seeding}
                onClick={() =>
                  act(value, `/api/tick?advance=${encodeURIComponent(value)}`)
                }
                title={
                  value === "next_salary_window"
                    ? "Jump to the next salary window (1st of the month, IST)"
                    : `Advance the simulation clock by ${value}`
                }
              >
                {busy === value ? "" : label}
              </Button>
            ))}
          </div>
        </div>

        {connected === false && <Disconnected />}

        {connected && seeding && <Warming stage={health?.seeding_stage} />}

        {/* An action that failed while the API is otherwise reachable — a tick
            that 400d, say. Dropping this would make a failed button click look
            exactly like a successful one. */}
        {connected !== false && error && (
          <div
            role="alert"
            className="flex items-start gap-2 rounded-2xl border border-red/30 bg-redwash px-4 py-3 text-[12px] text-red"
          >
            <IconAlert className="mt-px h-4 w-4 shrink-0" />
            <span className="min-w-0 break-words">{error}</span>
          </div>
        )}

        <main className="rise pb-4">
          {!board ? (
            connected === false ? (
              <Card>
                <Empty>
                  No data — the dashboard is not connected to an API.
                </Empty>
              </Card>
            ) : (
              <LoadingBoard />
            )
          ) : tab === "dashboard" ? (
            <Dashboard board={board} onDrill={drill} />
          ) : tab === "queue" ? (
            <Queue
              records={records}
              onOpen={openAudit}
              search={search}
              onSearch={setSearch}
              filter={queueFilter}
              onFilter={setQueueFilter}
            />
          ) : tab === "promises" ? (
            <Promises onOpenRecord={openAudit} />
          ) : tab === "rules" ? (
            <RulesStudio onChanged={refresh} />
          ) : tab === "human" ? (
            <HumanQueue items={queue} onOpen={openAudit} />
          ) : openRecord ? (
            <AuditTrail recordId={openRecord} onBack={() => setTab("queue")} />
          ) : (
            <Card title="Audit trail">
              <Empty>Pick a record from the recovery queue.</Empty>
            </Card>
          )}
        </main>

        <footer className="px-1 pb-2 text-[11px] leading-relaxed text-dim">
          Real Razorpay APIs, real error-code shapes, real payment links.{" "}
          <strong className="font-semibold text-muted">
            Customer response is modelled
          </strong>{" "}
          — per-cause success probabilities, stated openly rather than presented
          as live conversion data. Outcomes reach the scoreboard only through
          signed, verified webhooks and the attribution chain.
        </footer>
      </div>
    </div>
  );
}

function Wordmark({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2.5 px-2">
      <span
        aria-hidden="true"
        className="grid h-9 w-9 shrink-0 place-items-center rounded-2xl bg-greendeep"
      >
        <svg viewBox="0 0 24 24" className="h-5 w-5 text-ondeep" fill="none">
          <circle cx="12" cy="12" r="8.5" stroke="currentColor" strokeWidth="1.75" />
          <circle cx="12" cy="12" r="3.5" fill="currentColor" />
        </svg>
      </span>
      {!compact && (
        <span className="text-[17px] font-bold tracking-tight text-ink">
          Reclaim<span className="text-green">AI</span>
        </span>
      )}
    </div>
  );
}

// The live theme is DOM state, not React state — the pre-paint script in the
// layout may already have set it, and the OS can change it under us. Reading it
// through an external store keeps one source of truth and avoids a
// setState-in-effect on every mount.
const MEDIA = "(prefers-color-scheme: dark)";

function subscribeTheme(cb: () => void) {
  const mq = window.matchMedia(MEDIA);
  mq.addEventListener("change", cb);
  window.addEventListener("reclaim-theme", cb);
  return () => {
    mq.removeEventListener("change", cb);
    window.removeEventListener("reclaim-theme", cb);
  };
}

function readTheme() {
  const attr = document.documentElement.dataset.theme;
  if (attr === "dark") return true;
  if (attr === "light") return false;
  return window.matchMedia(MEDIA).matches;
}

function ThemeToggle() {
  // The prerendered snapshot has no DOM to ask, so it assumes light and
  // corrects itself on hydration.
  const dark = useSyncExternalStore(subscribeTheme, readTheme, () => false);

  const toggle = () => {
    const next = !dark;
    document.documentElement.dataset.theme = next ? "dark" : "light";
    try {
      localStorage.setItem("reclaim-theme", next ? "dark" : "light");
    } catch {
      /* private mode, embedded viewer — the toggle still works for this page */
    }
    window.dispatchEvent(new Event("reclaim-theme"));
  };

  return (
    <button
      type="button"
      onClick={toggle}
      aria-pressed={dark}
      className="flex w-full cursor-pointer items-center gap-2.5 rounded-2xl border border-line px-3 py-2.5 text-[13px] font-medium text-muted transition-colors duration-200 hover:bg-panel2 hover:text-ink"
    >
      {dark ? (
        <IconSun className="h-[18px] w-[18px] shrink-0" />
      ) : (
        <IconMoon className="h-[18px] w-[18px] shrink-0" />
      )}
      {dark ? "Light theme" : "Dark theme"}
    </button>
  );
}

function SystemCard({
  health,
  connected,
}: {
  health: Health | null;
  connected: boolean | null;
}) {
  return (
    <div className="rounded-3xl bg-greendeep p-4">
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-ondeep/75">
        System
      </p>
      {!health ? (
        <p className="mt-2 text-[12px] text-ondeep/70">
          {connected === false ? "No API connected." : "Checking…"}
        </p>
      ) : (
        <dl className="mt-2.5 space-y-1.5 text-[11px]">
          <SysRow
            k="Mode"
            v={health.dry_run ? "DRY_RUN" : "LIVE"}
            ok={!health.dry_run}
          />
          <SysRow
            k="Autopilot"
            v={health.autopilot_enabled ? "armed" : "OFF"}
            ok={health.autopilot_enabled}
          />
          <SysRow
            k="Layer 2"
            v={
              health.anthropic_credentials || health.gemini_credentials
                ? "on"
                : "deterministic only"
            }
            ok={health.anthropic_credentials || health.gemini_credentials}
          />
          <SysRow
            k="Razorpay"
            v={health.razorpay_credentials ? "test keys" : "absent"}
            ok={health.razorpay_credentials}
          />
        </dl>
      )}
    </div>
  );
}

function SysRow({ k, v, ok }: { k: string; v: string; ok: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-ondeep/65">{k}</dt>
      <dd className="flex items-center gap-1.5">
        <span
          aria-hidden="true"
          className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-greensoft" : "bg-amber"}`}
        />
        <span className="num font-semibold on-deep">{v}</span>
      </dd>
    </div>
  );
}

function LoadingBoard() {
  // Reserves roughly the shape the scoreboard will take, so the page does not
  // jump when the four fetches land.
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-12" aria-busy="true">
      <span className="sr-only">Loading the scoreboard…</span>
      {[0, 1, 2, 3].map((i) => (
        <Skeleton key={i} className="h-[152px] md:col-span-6 xl:col-span-3" />
      ))}
      <Skeleton className="h-[92px] md:col-span-12" />
      <Skeleton className="h-[380px] md:col-span-12 lg:col-span-8" />
      <Skeleton className="h-[380px] md:col-span-12 lg:col-span-4" />
    </div>
  );
}

function Warming({ stage }: { stage?: string | null }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-start gap-2.5 rounded-3xl border border-amber/30 bg-amberwash px-4 py-3.5 text-[12px]"
    >
      <svg
        viewBox="0 0 24 24"
        aria-hidden="true"
        className="mt-px h-4 w-4 shrink-0 animate-spin text-amber"
      >
        <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="3" opacity="0.25" />
        <path d="M12 3a9 9 0 0 1 9 9" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
      </svg>
      <div className="min-w-0">
        <p className="font-semibold text-amber">
          {stage ? `Working — ${stage}.` : "Working."} These numbers are still
          filling in.
        </p>
        <p className="mt-1 text-muted">
          The agent is walking the demo arc: 120 records detected, every one
          diagnosed, then the whole schedule replayed so deferred work lands.
          With layer 2 on that is about two minutes, because a free model tier
          is paced. Nothing is missing — the zeroes below mean{" "}
          <em>not yet</em>, not <em>nothing was recovered</em>.
        </p>
        <p className="mt-1 text-dim">Refreshing automatically.</p>
      </div>
    </div>
  );
}

function Disconnected() {
  // This page is served by Vercel; the API lives somewhere else. Saying "run
  // `reclaim serve`" on a public URL is advice for a machine the reader does
  // not have, so name the address that was actually tried instead.
  const base = process.env.NEXT_PUBLIC_API_BASE;
  return (
    <div
      role="status"
      className="rounded-3xl border border-amber/30 bg-amberwash px-4 py-3.5 text-[12px]"
    >
      <p className="flex items-center gap-2 font-semibold text-amber">
        <IconAlert className="h-4 w-4 shrink-0" />
        Dashboard loaded. No API connected.
      </p>
      <p className="mt-1.5 text-muted">
        This page is the front end only. It reads its numbers from the ReclaimAI
        API, which is deployed separately and is not reachable at{" "}
        <span className="num text-ink">{base || "this origin"}</span>.
      </p>
      <p className="mt-1.5 text-dim">
        Running it locally?{" "}
        <span className="num text-muted">python -m reclaim.cli demo</span> then{" "}
        <span className="num text-muted">python -m reclaim.cli serve</span> — the
        API serves this same dashboard on its own port, no separate front end
        needed.
      </p>
    </div>
  );
}

function HumanQueue({
  items,
  onOpen,
}: {
  items: QueueItem[];
  onOpen: (id: string) => void;
}) {
  const total = items.reduce((n, i) => n + i.amount_paise, 0);
  return (
    <Card
      title={`${items.length} escalations waiting`}
      hint="Amounts above the value ceiling, causes the policy table refuses to automate, and diagnoses the confidence floor would not accept."
      right={
        <div className="text-right">
          <p className="num text-[22px] font-bold leading-none text-amber">
            {(total / 100).toLocaleString("en-IN", {
              style: "currency",
              currency: "INR",
              maximumFractionDigits: 0,
            })}
          </p>
          <p className="mt-1 text-[11px] text-dim">held for a person</p>
        </div>
      }
    >
      {items.length === 0 ? (
        <Empty>Nothing escalated.</Empty>
      ) : (
        <div className="-mx-5 overflow-x-auto">
          <table className="w-full min-w-[720px] text-[13px]">
            <thead>
              <tr className="border-b border-line text-[10px] uppercase tracking-wider text-dim">
                <th scope="col" className="px-5 py-2.5 text-left font-semibold">
                  Record
                </th>
                <th scope="col" className="px-2 py-2.5 text-right font-semibold">
                  Amount
                </th>
                <th scope="col" className="px-2 py-2.5 text-left font-semibold">
                  Cause
                </th>
                <th scope="col" className="px-5 py-2.5 text-left font-semibold">
                  Why a human has it
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr
                  key={i.id}
                  onClick={() => onOpen(i.record_id)}
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onOpen(i.record_id);
                    }
                  }}
                  className="cursor-pointer border-b border-line/60 transition-colors duration-200 hover:bg-panel2"
                >
                  <td className="num px-5 py-2.5 font-medium">{i.record_id}</td>
                  <td className="num px-2 py-2.5 text-right">
                    {i.amount_display}
                  </td>
                  <td className="px-2 py-2.5 text-[12px]">
                    {i.root_cause ?? "—"}
                  </td>
                  <td className="px-5 py-2.5 text-[12px] text-muted">
                    {i.reason}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
