/**
 * Minimal typed loader for the PayPal JavaScript SDK v6 ("web SDK").
 * Flow (per PayPal's v6 docs): load /web-sdk/v6/core → createInstance({clientId}) → findEligibleMethods →
 * createPayPalOneTimePaymentSession({onApprove, onCancel, onError}) → session.start({presentationMode}, orderPromise)
 * where orderPromise resolves to { orderId } created by OUR backend.
 */

export type OnApproveData = { orderId: string; payerId?: string };
export type OnCancelData = { orderId?: string };
export type PayPalSdkError = { code?: string; message?: string };

export type PaymentSession = {
  start(options: { presentationMode: "auto" | "popup" | "modal" | "redirect" }, orderPromise: Promise<{ orderId: string }>): Promise<void>;
};

export type SdkInstance = {
  findEligibleMethods(options?: { currencyCode?: string; amount?: string }): Promise<{ isEligible(method: string): boolean }>;
  createPayPalOneTimePaymentSession(options: {
    onApprove: (data: OnApproveData) => Promise<void> | void;
    onCancel?: (data: OnCancelData) => void;
    onError?: (error: PayPalSdkError) => void;
  }): PaymentSession;
};

declare global {
  interface Window {
    paypal?: { createInstance(options: { clientId: string; components: string[]; pageType?: string; locale?: string }): Promise<SdkInstance> };
  }
}

declare module "react" {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace JSX {
    interface IntrinsicElements {
      "paypal-button": React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & { type?: string };
    }
  }
}

const loading = new Map<string, Promise<void>>();

export function loadPayPalSdk(src: string): Promise<void> {
  if (window.paypal) return Promise.resolve();
  if (!loading.has(src)) {
    loading.set(
      src,
      new Promise<void>((resolve, reject) => {
        const s = document.createElement("script");
        s.src = src;
        s.async = true;
        s.onload = () => resolve();
        s.onerror = () => {
          loading.delete(src);
          reject(new Error("Could not load the PayPal SDK"));
        };
        document.head.appendChild(s);
      }),
    );
  }
  return loading.get(src)!;
}

const instances = new Map<string, Promise<SdkInstance>>();

export async function getPayPalInstance(sdkUrl: string, clientId: string): Promise<SdkInstance> {
  await loadPayPalSdk(sdkUrl);
  if (!window.paypal) throw new Error("PayPal SDK did not initialise");
  const key = `${sdkUrl}|${clientId}`;
  if (!instances.has(key)) {
    instances.set(key, window.paypal.createInstance({ clientId, components: ["paypal-payments"], pageType: "checkout" }));
  }
  return instances.get(key)!;
}
