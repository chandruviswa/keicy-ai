import { CreditCard, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, formatMoney, type CaptureResult, type PaymentConfig } from "../api";
import { getPayPalInstance, type PaymentSession } from "../paypal";

type Props = {
  purchaseOrderId: string;
  amount: string;
  currency: string;
  poNumber: string;
  onFinished: (result: CaptureResult | null) => void;
};

/** PayPal v6 one-time checkout for an approved purchase order. The amount shown is the server's. */
export function PayPalCheckout({ purchaseOrderId, amount, currency, poNumber, onFinished }: Props) {
  const [config, setConfig] = useState<PaymentConfig | null>(null);
  const [session, setSession] = useState<PaymentSession | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "ineligible" | "working" | "error">("loading");
  const [message, setMessage] = useState<string | null>(null);
  const currentOrder = useRef<string | null>(null);
  const finished = useRef(onFinished);
  finished.current = onFinished;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const cfg = await api.paymentConfig();
        if (cancelled) return;
        setConfig(cfg);
        if (!cfg.configured || !cfg.client_id || !cfg.sdk_url) {
          setState("error");
          setMessage("PayPal isn't configured on the server (PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET).");
          return;
        }
        const sdk = await getPayPalInstance(cfg.sdk_url, cfg.client_id);
        const eligibility = await sdk.findEligibleMethods({ currencyCode: currency });
        if (cancelled) return;
        if (!eligibility.isEligible("paypal")) {
          setState("ineligible");
          return;
        }
        setSession(
          sdk.createPayPalOneTimePaymentSession({
            onApprove: async ({ orderId }) => {
              setState("working");
              setMessage("Confirming payment with PayPal…");
              try {
                const result = await api.capturePayPalOrder(orderId);
                setMessage(result.status === "FAILED" ? result.message : null);
                setState(result.status === "FAILED" ? "ready" : "working");
                finished.current(result);
              } catch (e) {
                setState("ready");
                setMessage(e instanceof Error ? e.message : String(e));
                finished.current(null);
              }
            },
            onCancel: async (data) => {
              const id = data?.orderId ?? currentOrder.current;
              if (id) await api.cancelCheckout(id).catch(() => undefined);
              setState("ready");
              setMessage("Checkout cancelled — nothing was charged. You can try again.");
              finished.current(null);
            },
            onError: (err) => {
              setState("ready");
              setMessage(`PayPal error: ${err?.message ?? err?.code ?? "unknown"}`);
            },
          }),
        );
        setState("ready");
      } catch (e) {
        if (!cancelled) {
          setState("error");
          setMessage(e instanceof Error ? e.message : String(e));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [currency]);

  async function pay() {
    if (!session) return;
    setMessage(null);
    // Not awaited before start(): PayPal needs the click's user activation to open its window.
    const orderPromise = api.createPayPalOrder(purchaseOrderId).then((o) => {
      currentOrder.current = o.orderId;
      return { orderId: o.orderId };
    });
    orderPromise.catch((e) => setMessage(e instanceof Error ? e.message : String(e)));
    try {
      await session.start({ presentationMode: "auto" }, orderPromise);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="rounded-[var(--radius-control)] border border-line bg-ink-50/60 p-4 sm:p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-medium"><CreditCard size={15} /> Pay {poNumber}</span>
        <span className="text-xl font-semibold tabular-nums">{formatMoney(amount, currency)}</span>
      </div>
      <p className="mt-1 text-xs text-ink-500">Amount set by the approved purchase order{config ? ` · PayPal ${config.mode}` : ""}.</p>

      <div className="mt-3 min-h-11">
        {state === "loading" && <span className="flex items-center gap-2 text-sm text-ink-500"><Loader2 size={15} className="animate-spin" /> Loading PayPal…</span>}
        {state === "ineligible" && <p className="text-sm text-canary-700">PayPal isn't available for {currency} in this browser.</p>}
        {(state === "ready" || state === "working") && (
          <div className={state === "working" ? "pointer-events-none opacity-50" : ""}>
            <paypal-button type="pay" onClick={pay} />
          </div>
        )}
      </div>

      {message && <p className={`mt-2 text-sm ${state === "error" ? "text-stamp-700" : "text-ink-700"}`}>{message}</p>}
    </div>
  );
}
