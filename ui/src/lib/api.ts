// One place that knows how to talk to the backend.
//
// Relative URLs in production, because FastAPI serves this bundle from its own
// origin. NEXT_PUBLIC_API_BASE points at the API during `next dev`, where the
// UI runs on :3000 and the API on :8000.

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

// A free instance is asleep until something knocks, and the knock itself is
// what wakes it — so the first request after an idle period can take the better
// part of a minute. Without a timeout that request hangs the page forever on a
// spinner; with too short a timeout it gives up on an API that was about to
// answer. 75 seconds is longer than a cold start and shorter than a visitor's
// patience, and the caller retries either way.
const READ_TIMEOUT_MS = 75_000;

// Writes only start the work now — the API answers 202 and the dashboard polls.
// Nothing here should ever be slow.
const WRITE_TIMEOUT_MS = 20_000;

class HttpError extends Error {
  constructor(readonly status: number, readonly path: string, readonly detail?: string) {
    super(detail ? `${path} -> ${status}: ${detail}` : `${path} -> ${status}`);
  }
}

async function request<T>(path: string, init: RequestInit, timeout: number): Promise<T> {
  // AbortSignal.timeout is not in every browser a demo might be opened in, so
  // fall back to a controller rather than throwing on the feature test itself.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(`${BASE}${path}`, {
      ...init,
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) {
      let detail: string | undefined;
      try {
        detail = (await response.json())?.detail;
      } catch {
        // A proxy error page is not JSON. The status code is the message.
      }
      throw new HttpError(response.status, path, detail);
    }
    return (await response.json()) as T;
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new Error(`${path} timed out after ${Math.round(timeout / 1000)}s`);
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

export async function get<T>(path: string): Promise<T> {
  return request<T>(path, {}, READ_TIMEOUT_MS);
}

export async function post<T>(path: string): Promise<T> {
  return request<T>(path, { method: "POST" }, WRITE_TIMEOUT_MS);
}

// 409 means a batch is already running — the request was understood and
// refused, which is information, not a failure to surface as a red banner.
export const isBusy = (e: unknown) => e instanceof HttpError && e.status === 409;

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

// Mirrors BaselineResult.as_dict() in reclaim/baseline.py, field for field.
export type Baseline = {
  label: string;
  records: number;
  at_risk_paise: number;
  recovered_paise: number;
  at_risk_display: string;
  recovered_display: string;
  records_recovered: number;
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

export type SnapshotHeader = {
  built_at: string;
  layer_2: boolean;
  records: number;
  recovered_paise: number;
  recovered_display: string;
  records_recovered: number;
  recovery_rate: number;
};

export type Health = {
  ok: boolean;
  // The one field the dashboard could never infer for itself: whether the API
  // is presently building a batch. Optional, because an older API deployment
  // does not send it and the page must still work against one.
  seeding?: boolean;
  seeding_stage?: string | null;
  seeding_since?: string | null;
  snapshot?: SnapshotHeader | null;
  dry_run: boolean;
  autopilot_enabled: boolean;
  razorpay_credentials: boolean;
  anthropic_credentials: boolean;
  gemini_credentials: boolean;
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
