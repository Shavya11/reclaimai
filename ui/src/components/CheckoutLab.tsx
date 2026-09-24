"use client";

// Pay for real, fail on purpose, and hand the failure to the agent.
//
// The Classify mode starts from a description somebody typed. This one starts
// from Razorpay: a real test-mode order, real Checkout, and the test bank's
// Failure button. The page only reports WHICH payment failed — the server
// fetches the payment back from Razorpay and diagnoses the error Razorpay
// stored, so nothing the agent reads here was written by the visitor.

import { useEffect, useState } from "react";

import { Trace, get, postJSON } from "@/lib/api";
import { Badge, Button, Card, Empty } from "@/components/ui";
import { TraceStrip } from "@/components/Trace";

const RUPEE = "₹";
const CHECKOUT_JS = "https://checkout.razorpay.com/v1/checkout.js";

type Config = { available: boolean; key_id: string };
type Order = { order_id: string; amount_paise: number; currency: string; key_id: string };
type RazorpayFacts = {
  payment_id: string;
  order_id: string;
  amount_paise: number;
  method?: string | null;
  error_code?: string | null;
  error_reason?: string | null;
  error_description?: string | null;
  error_source?: string | null;
  error_step?: string | null;
};
type Imported = Trace & { razorpay: RazorpayFacts; already_imported?: boolean };

type RazorpayFailure = {
  error?: { metadata?: { payment_id?: string; order_id?: string } };
};
type RazorpayInstance = {
  open: () => void;
  close: () => void;
  on: (event: "payment.failed", cb: (r: RazorpayFailure) => void) => void;
};
declare global {
  interface Window {
    Razorpay?: new (options: Record<string, unknown>) => RazorpayInstance;
  }
}

let scriptLoading: Promise<void> | null = null;

function loadCheckout(): Promise<void> {
  if (window.Razorpay) return Promise.resolve();
  scriptLoading ??= new Promise<void>((resolve, reject) => {
    const s = document.createElement("script");
    s.src = CHECKOUT_JS;
    s.onload = () => resolve();
    s.onerror = () => {
      scriptLoading = null;
      reject(new Error("Could not load Razorpay Checkout."));
    };
    document.body.appendChild(s);
  });
  return scriptLoading;
}

type Phase = "idle" | "opening" | "paying" | "importing" | "paid" | "done";

export function CheckoutLab({ onCommitted }: { onCommitted?: () => void }) {
  const [config, setConfig] = useState<Config | null>(null);
  const [rupees, setRupees] = useState(4000);
  const [phase, setPhase] = useState<Phase>("idle");
  const [result, setResult] = useState<Imported | null>(null);
  const [paidId, setPaidId] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    get<Config>("/api/sandbox/checkout")
      .then(setConfig)
      .catch(() => setConfig({ available: false, key_id: "" }));
  }, []);

  const importFailure = async (orderId: string, paymentId: string) => {
    setPhase("importing");
    try {
      const imported = await postJSON<Imported>("/api/sandbox/checkout/failed", {
        order_id: orderId,
        payment_id: paymentId,
      });
      setResult(imported);
      setPhase("done");
      onCommitted?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPhase("idle");
    }
  };

  const pay = async () => {
    setError("");
    setResult(null);
    setPaidId("");
    setPhase("opening");
    try {
      const [order] = await Promise.all([
        postJSON<Order>("/api/sandbox/checkout/order", {
          amount_paise: Math.round(rupees) * 100,
        }),
        loadCheckout(),
      ]);
      if (!window.Razorpay) throw new Error("Razorpay Checkout did not load.");

      let failed = false;
      const rzp = new window.Razorpay({
        key: order.key_id,
        order_id: order.order_id,
        amount: order.amount_paise,
        currency: order.currency,
        name: "ReclaimAI test shop",
        description: "Test mode — no real money moves",
        theme: { color: "#0f7a4a" },
        handler: (r: { razorpay_payment_id: string }) => {
          setPaidId(r.razorpay_payment_id);
          setPhase("paid");
        },
        modal: {
          ondismiss: () => {
            if (!failed) setPhase("idle");
          },
        },
      });
      rzp.on("payment.failed", (r) => {
        const paymentId = r.error?.metadata?.payment_id;
        if (!paymentId || failed) return;
        failed = true;
        // Checkout would offer a retry here. Closing instead, because the
        // failure is the thing the visitor came to hand over.
        rzp.close();
        void importFailure(order.order_id, paymentId);
      });
      setPhase("paying");
      rzp.open();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPhase("idle");
    }
  };

  const busy = phase === "opening" || phase === "paying" || phase === "importing";

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
      <div className="xl:col-span-5">
        <Card title="Fail a real test payment" hint="Razorpay test mode, not a description">
          {config && !config.available ? (
            <Empty>
              This server has no <code>rzp_test_</code> keys, so it cannot open
              Checkout. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET to use this mode.
            </Empty>
          ) : (
            <>
              <label className="block">
                <span className="text-[11px] font-medium text-dim">Amount ({RUPEE})</span>
                <input
                  type="number"
                  min={1}
                  value={rupees}
                  onChange={(e) => setRupees(Math.max(1, Number(e.target.value)))}
                  className="num mt-1.5 w-full rounded-2xl border border-line bg-panel2 px-3 py-2 text-[13px] text-ink outline-none focus:border-linestrong"
                />
              </label>

              <ol className="mt-4 list-decimal space-y-1.5 pl-5 text-[12px] leading-relaxed text-muted">
                <li>Open Checkout. A real test order is created on Razorpay first.</li>
                <li>
                  Pay any test way: <strong className="text-ink">Netbanking</strong>{" "}
                  with any bank, or card{" "}
                  <span className="num text-ink">5267 3181 8797 5449</span> with any
                  future expiry and CVV.
                </li>
                <li>
                  On the test bank page, press <strong className="text-ink">Failure</strong>.
                </li>
              </ol>

              <div className="mt-4 flex flex-wrap gap-2">
                <Button
                  variant="primary"
                  busy={busy}
                  disabled={busy || !config}
                  onClick={pay}
                >
                  {phase === "importing"
                    ? "Fetching the failure from Razorpay…"
                    : `Open Razorpay Checkout · ${RUPEE}${rupees.toLocaleString("en-IN")}`}
                </Button>
              </div>

              <p className="mt-3 text-[11px] leading-relaxed text-dim">
                The page only says which payment failed. The server fetches it back
                from Razorpay and checks it belongs to an order opened here, so the
                error the agent diagnoses is the one Razorpay recorded. The record is
                committed like any other and counted apart from the published figures.
              </p>
            </>
          )}

          {error && (
            <p className="mt-3 rounded-2xl border border-red/30 bg-redwash p-3 text-[12px] text-red">
              {error}
            </p>
          )}
        </Card>
      </div>

      <div className="xl:col-span-7">
        <Card title="What the agent did with it" hint="read back off the audit log">
          {phase === "paid" ? (
            <Empty>
              That payment went through ({paidId}), so there is nothing to recover.
              Try again and press Failure on the bank page.
            </Empty>
          ) : !result ? (
            <Empty>Open Checkout and fail a payment to see it here.</Empty>
          ) : (
            <>
              <dl className="mb-4 grid grid-cols-2 gap-x-4 gap-y-1.5 rounded-2xl border border-line bg-panel2 p-3 text-[12px]">
                {(
                  [
                    ["Payment", result.razorpay.payment_id],
                    ["Order", result.razorpay.order_id],
                    ["Method", result.razorpay.method],
                    ["Error code", result.razorpay.error_code],
                    ["Error reason", result.razorpay.error_reason],
                    ["Failed at", result.razorpay.error_step],
                  ] as Array<[string, string | null | undefined]>
                ).map(([k, v]) => (
                  <div key={k} className="min-w-0">
                    <dt className="text-[10px] text-dim">{k}</dt>
                    <dd className="num truncate text-ink">{v || "—"}</dd>
                  </div>
                ))}
              </dl>
              <TraceStrip
                stages={result.trace}
                verdict={result.verdict}
                header={
                  <Badge tone="green">
                    {result.already_imported
                      ? `already imported as ${result.record_id}`
                      : `committed as ${result.record_id}`}
                  </Badge>
                }
              />
              <p className="mt-4 rounded-2xl border border-line bg-panel2 p-3 text-[12px] leading-relaxed text-muted">
                <strong className="font-semibold text-ink">{result.record_id}</strong>{" "}
                is now a record like any other: {RUPEE}
                {(result.razorpay.amount_paise / 100).toLocaleString("en-IN")} at
                risk, on the dashboard under &ldquo;Submitted from the dashboard&rdquo;,
                in the recovery queue, and with its own audit trail. It obeys all
                fourteen guardrails, the same as a record from Classify.
              </p>
            </>
          )}
        </Card>
      </div>
    </div>
  );
}
