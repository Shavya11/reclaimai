// One place that knows how to talk to the backend.
//
// Relative URLs in production, because FastAPI serves this bundle from its own
// origin. NEXT_PUBLIC_API_BASE points at the API during `next dev`, where the
// UI runs on :3000 and the API on :8000.

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

export async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path} -> ${response.status}`);
  return response.json() as Promise<T>;
}

export async function post<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`, { method: "POST" });
  if (!response.ok) throw new Error(`${path} -> ${response.status}`);
  return response.json() as Promise<T>;
}

export type CauseLine = {
  root_cause: string;
  records: number;
  recovered_records: number;
  at_risk_paise: number;
  recovered_paise: number;
  contacts: number;
  rate: number;
  value_rate: number;
};

export type Scoreboard = {
  label: string;
  records: number;
  at_risk_paise: number;
  recovered_paise: number;
  open_paise: number;
  unrecoverable_paise: number;
  at_risk_display: string;
  recovered_display: string;
  open_display: string;
  unrecoverable_display: string;
  at_risk_short: string;
  recovered_short: string;
  records_recovered: number;
  records_open: number;
  records_unrecoverable: number;
  recovery_rate: number;
  record_recovery_rate: number;
  by_root_cause: CauseLine[];
  guardrails_fired: Record<string, number>;
  guardrails_records: Record<string, number>;
  guardrails_total: number;
  escalations: number;
  interventions: number;
  contacts: number;
  silent_retries: number;
  contacts_per_recovery: number;
  webhooks_attributed: number;
  balances: boolean;
};

export type Block = {
  guardrail: string;
  reason: string;
  deferred_until: string | null;
  at: string | null;
};

export type RecordRow = {
  id: string;
  leak_type: string;
  amount_paise: number;
  amount_display: string;
  counterparty_id: string;
  source_ref: string;
  detected_at: string | null;
  state: string;
  attempts: number;
  next_action_at: string | null;
  root_cause: string | null;
  issuer_bank: string | null;
  method: string | null;
  last_action: string | null;
  last_action_at: string | null;
  last_policy_ref: string | null;
  last_result: string | null;
  recovered_paise: number;
  blocks: Block[];
};

export type Intervention = {
  id: number;
  action_type: string;
  channel: string | null;
  policy_ref: string;
  attempt_number: number;
  scheduled_for: string | null;
  executed_at: string | null;
  razorpay_ref: string | null;
  outcome: string | null;
  result: string | null;
  recovered_paise: number;
};

export type RecordDetail = RecordRow & {
  raw_signals: Record<string, unknown>;
  customer: {
    id: string;
    opted_out: boolean;
    on_dnd: boolean;
    successful_payments_lifetime: number;
  } | null;
  interventions: Intervention[];
};

export type AuditEvent = {
  id: number;
  stage: string;
  outcome: string;
  guardrail: string | null;
  reason: string;
  payload: Record<string, unknown>;
  deferred_until: string | null;
  at: string | null;
};

export type Baseline = {
  label: string;
  recovered_display: string;
  recovery_rate: number;
  record_recovery_rate: number;
  contacts: number;
  contacts_per_recovery: number;
  contacts_to_opted_out: number;
  contacts_to_dnd: number;
  contacts_in_quiet_hours: number;
  customers_over_frequency_cap: number;
  retries_against_never_retry: number;
  compliance_breaches: number;
};

export type GapReason = {
  reason: string;
  label: string;
  records: number;
  paise: number;
  display: string;
};

export type Comparison = {
  baseline: Baseline;
  ours: Scoreboard;
  gap: {
    total: { records: number; paise: number; display: string };
    reasons: GapReason[];
    deliberate_paise: number;
    recoverable_with_layer_2_paise: number;
    still_open_paise: number;
  };
};

export type Health = {
  ok: boolean;
  dry_run: boolean;
  autopilot_enabled: boolean;
  razorpay_credentials: boolean;
  anthropic_credentials: boolean;
  model: string;
  clock: string;
  time_travelled: boolean;
};

export type QueueItem = {
  id: number;
  record_id: string;
  reason: string;
  amount_display: string;
  amount_paise: number;
  root_cause: string | null;
  raised_at: string | null;
};

export type GuardrailFeed = {
  registered: string[];
  fired: Record<string, number>;
  total: number;
  blocks: Array<{
    record_id: string;
    guardrail: string;
    reason: string;
    deferred_until: string | null;
    action_type: string | null;
    policy_ref: string | null;
    at: string | null;
  }>;
};

export const fmtTime = (iso: string | null | undefined) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
};

export const pct = (n: number) => `${(n * 100).toFixed(1)}%`;
