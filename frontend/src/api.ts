/** Typed client for the FastAPI backend (/api/v1). Money arrives as strings — never parse into floats for maths. */

const BASE = "/api/v1";

export type Health = {
  status: "ok" | "degraded";
  database: boolean;
  schema_version: string | null;
  llm: { cloud_configured: boolean; cloud_model: string; ollama_reachable: boolean; ollama_model: string };
  paypal: { configured: boolean; mode: string };
};

export type Vendor = { id: string; name: string; contact_email: string; country: string; rating: string; is_active: boolean };

export type Offer = {
  id: string;
  vendor_id: string;
  vendor_name: string;
  product_id: string;
  unit_price: string;
  currency: string;
  available_quantity: number;
  min_order_quantity: number;
  lead_time_days: number;
  valid_until: string;
  is_expired: boolean;
  is_active: boolean;
  specs: Record<string, unknown>;
};

export type StarterCatalog = {
  vendors: { name: string; contact_email: string; country: string; rating: string; offers: number; imported: boolean }[];
  products: { sku: string; name: string; category: string; description: string; offers: number; imported: boolean }[];
};

export type VendorInput = { name: string; contact_email: string; country: string; rating: string };
export type ProductInput = { sku: string; name: string; category: string; description: string; unit: string; specs: Record<string, unknown> };
export type OfferInput = {
  vendor_id: string;
  product_id: string;
  unit_price: string;
  currency: string;
  available_quantity: number;
  min_order_quantity: number;
  lead_time_days: number;
  valid_until: string;
  specs: Record<string, unknown>;
};

export type Product = {
  id: string;
  sku: string;
  name: string;
  category: string;
  description: string;
  unit: string;
  specs: Record<string, unknown>;
  offers: Offer[];
};

export type ProcurementRequest = {
  id: string;
  requester_id: string;
  raw_text: string;
  title: string | null;
  status: string;
  budget: string | null;
  currency: string;
  deadline: string | null;
  created_at: string;
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// ---------- session token ----------
const TOKEN_KEY = "procura.token";
let token: string | null = (() => {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
})();
export const session = {
  get: () => token,
  set(t: string | null) {
    token = t;
    try {
      if (t) localStorage.setItem(TOKEN_KEY, t);
      else localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* private mode: keep the token in memory only */
    }
  },
};
/** Fired when the server says the session is no longer valid, so the app can return to sign-in. */
export const SESSION_EXPIRED = "procura:session-expired";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json", ...((init?.headers as Record<string, string>) ?? {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const r = await fetch(`${BASE}${path}`, { ...init, headers });
  if (r.status === 401 && token && !path.startsWith("/auth/login")) {
    session.set(null);
    window.dispatchEvent(new Event(SESSION_EXPIRED));
  }
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    const detail = body.detail;
    const msg = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : r.statusText;
    throw new ApiError(r.status, msg || `Request failed (${r.status})`);
  }
  return r.json();
}

export type AgentRunSummary = {
  id: string;
  agent_name: string;
  status: "RUNNING" | "SUCCEEDED" | "FAILED" | "TIMED_OUT";
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
};

export type WorkflowStatus = {
  id: string;
  status: string;
  is_running: boolean;
  allowed_next: string[];
  last_reason: string | null;
  clarification_questions: string[];
  purchase_order_id: string | null;
  runs: AgentRunSummary[];
};

export type CandidateLine = { offer_id: string; sku: string; quantity: number; unit_price: string; line_total: string; lead_time_days: number };
export type Candidate = {
  vendor_id: string;
  vendor_name: string;
  vendor_rating: string;
  lines: CandidateLine[];
  currency: string;
  subtotal: string;
  tax_amount: string;
  total: string;
  lead_time_days: number;
  delivery_date: string;
  within_budget: boolean;
  scores: Record<string, string>;
  score: string;
  rank: number;
};

export type Offers = {
  requirement: { items: { product_name: string; quantity: number; specifications: Record<string, unknown> }[]; budget: string | null; currency: string; deadline: string | null } | null;
  items: { product_name: string; quantity: number; products: { sku: string; name: string; reason: string }[]; eligible_quotes: number }[];
  ranking: Candidate[];
  recommended: Candidate | null;
  excluded: string[];
  explanation: { summary: string; reasons: string[]; risks: string[] } | null;
  explanation_source: string | null;
  purchase_order: { purchase_order_id: string; po_number: string; status: string; subtotal: string; tax_amount: string; total: string; currency: string } | null;
  po_summary: string | null;
  finance_report: { checks: { name: string; passed: boolean; detail: string }[]; requires_senior_approval: boolean; remaining_budget: string | null } | null;
};

export type PurchaseOrder = {
  id: string;
  po_number: string;
  request_id: string;
  request_status: string | null;
  vendor_name: string | null;
  status: string;
  currency: string;
  subtotal: string;
  tax_amount: string;
  total: string;
  created_at: string;
  items: { id: string; description: string; quantity: number; unit_price: string; line_total: string }[];
  approvals: { decision: string; reason: string | null; decided_at: string }[];
};

export type WorkflowEvent = { seq: number; event_type: string; payload: Record<string, unknown>; created_at: string };

export type DashboardSummary = {
  scope: "mine" | "organisation";
  total_requests: number;
  active_workflows: number;
  pending_approvals: number;
  completed_purchases: number;
  total_transaction_value: string;
  pipeline: { key: string; label: string; count: number }[];
  money: {
    paid: string;
    paid_last_30d: string;
    paid_prev_30d: string;
    awaiting_approval: string;
    ready_to_pay: string;
    saved_vs_highest_quote: string;
    payments: number;
    average_order: string;
  };
  spend_by_day: { date: string; amount: string }[];
  spend_by_vendor: { vendor: string; amount: string; orders: number }[];
  spend_by_category: { category: string; amount: string }[];
  speed: { median_minutes_to_po: number | null; median_hours_to_decision: number | null; median_hours_to_complete: number | null };
  approvals: { approved: number; rejected: number; approval_rate: number | null };
  agents: { runs: number; succeeded: number; success_rate: number | null; median_seconds: number | null };
  catalogue: { vendors: number; products: number; live_offers: number; expiring_soon: number; out_of_stock: number; expired: number };
  recent_activity: (WorkflowEvent & { request_id: string; request_title: string | null })[];
  recent_purchase_orders: { id: string; po_number: string; vendor_name: string; status: string; total: string; currency: string; request_id: string }[];
};

export type User = { id: string; email: string; full_name: string; role: "requester" | "approver" | "admin" };
export type DemoAccounts = { enabled: boolean; password?: string; accounts: { email: string; name: string; role: string }[] };

export const api = {
  health: () => req<Health>("/health"),
  vendors: () => req<Vendor[]>("/vendors"),
  products: (params: { category?: string; q?: string } = {}) => {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][]);
    return req<Product[]>(`/products${qs.size ? `?${qs}` : ""}`);
  },
  createVendor: (body: VendorInput) => req<Vendor>("/vendors", { method: "POST", body: JSON.stringify(body) }),
  setVendorActive: (id: string, is_active: boolean) => req<Vendor>(`/vendors/${id}`, { method: "PATCH", body: JSON.stringify({ is_active }) }),
  createProduct: (body: ProductInput) => req<Product>("/products", { method: "POST", body: JSON.stringify(body) }),
  createOffer: (body: OfferInput) => req<Offer>("/offers", { method: "POST", body: JSON.stringify(body) }),
  updateOffer: (id: string, body: Partial<{ is_active: boolean; unit_price: string; available_quantity: number; valid_until: string }>) =>
    req<Offer>(`/offers/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  starterCatalog: () => req<StarterCatalog>("/catalog/starter"),
  importStarter: (body: { vendors?: string[]; skus?: string[] }) =>
    req<{ added: { vendors: number; products: number; offers: number } }>("/catalog/starter/import", { method: "POST", body: JSON.stringify(body) }),
  listRequests: () => req<ProcurementRequest[]>("/procurement/requests"),
  getRequest: (id: string) => req<ProcurementRequest>(`/procurement/requests/${id}`),
  createRequest: (body: { raw_text: string; title?: string; budget?: string; deadline?: string }) =>
    req<ProcurementRequest>("/procurement/requests", { method: "POST", body: JSON.stringify(body) }),
  updateRequest: (id: string, body: Partial<{ raw_text: string; budget: string; deadline: string }>) =>
    req<ProcurementRequest>(`/procurement/requests/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  execute: (id: string) => req<WorkflowStatus>(`/procurement/requests/${id}/execute`, { method: "POST" }),
  cancel: (id: string) => req<WorkflowStatus>(`/procurement/requests/${id}/cancel`, { method: "POST" }),
  status: (id: string) => req<WorkflowStatus>(`/procurement/requests/${id}/status`),
  offers: (id: string) => req<Offers>(`/procurement/requests/${id}/offers`),
  purchaseOrder: (id: string) => req<PurchaseOrder>(`/purchase-orders/${id}`),
  approve: (poId: string, note?: string) => req<PurchaseOrder>(`/purchase-orders/${poId}/approve`, { method: "POST", body: JSON.stringify({ note }) }),
  reject: (poId: string, reason: string) => req<PurchaseOrder>(`/purchase-orders/${poId}/reject`, { method: "POST", body: JSON.stringify({ reason }) }),
  login: (email: string, password: string) =>
    req<{ access_token: string; expires_at: string; user: User }>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  me: () => req<User>("/auth/me"),
  demoAccounts: () => req<DemoAccounts>("/auth/demo-accounts"),
  purchaseOrders: (status?: string) => req<PurchaseOrder[]>(`/purchase-orders${status ? `?status=${status}` : ""}`),
  approvals: () => req<ApprovalRow[]>("/approvals"),
  history: (requestId: string) => req<HistoryRow[]>(`/procurement/requests/${requestId}/history`),
  agentRuns: (params: { agent?: string; status?: string } = {}) => {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][]);
    return req<RunRow[]>(`/agents/runs${qs.size ? `?${qs}` : ""}`);
  },
  runLogs: (runId: string) => req<RunLogs>(`/agents/runs/${runId}/logs`),
  intelligence: () => req<Intelligence>("/intelligence/summary"),
  auditLogs: (params: { action?: string; entity_type?: string } = {}) => {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][]);
    return req<AuditRow[]>(`/audit/logs${qs.size ? `?${qs}` : ""}`);
  },
  dashboard: () => req<DashboardSummary>("/dashboard/summary"),
  paymentConfig: () => req<PaymentConfig>("/payments/config"),
  // Only the PO id is sent — the server prices the order from its own records.
  createPayPalOrder: (purchaseOrderId: string) =>
    req<{ orderId: string; payment_id: string; amount: string; currency: string; reused: boolean }>("/payments/create-order", {
      method: "POST",
      body: JSON.stringify({ purchase_order_id: purchaseOrderId }),
    }),
  capturePayPalOrder: (paypalOrderId: string) =>
    req<CaptureResult>("/payments/capture", { method: "POST", body: JSON.stringify({ paypal_order_id: paypalOrderId }) }),
  cancelCheckout: (paypalOrderId: string) => req<CaptureResult>("/payments/cancel", { method: "POST", body: JSON.stringify({ paypal_order_id: paypalOrderId }) }),
  payments: (requestId?: string) => req<PaymentRecord[]>(`/payments${requestId ? `?request_id=${requestId}` : ""}`),
};

export type PaymentConfig = { configured: boolean; client_id: string; currency: string; mode: "sandbox" | "live"; sdk_url: string | null };
export type CaptureResult = { payment_id: string; status: string; request_status: string; capture_id: string | null; message: string | null; duplicate: boolean; issues: string[] };
export type Receipt = {
  receipt_number: string;
  po_number: string;
  paypal_order_id: string;
  capture_id: string;
  amount: string;
  currency: string;
  paid_at: string | null;
  subtotal: string;
  tax_amount: string;
  lines: { description: string; quantity: number; unit_price: string; line_total: string }[];
};
export type PaymentRecord = {
  id: string;
  amount: string;
  currency: string;
  status: string;
  attempt: number;
  failure_reason: string | null;
  receipt: Receipt | null;
  reconciled_at: string | null;
  created_at: string;
  po_number: string | null;
  request_id: string | null;
  request_title: string | null;
  paypal_orders: { paypal_order_id: string; status: string }[];
  captures: { capture_id: string; status: string; amount: string; currency: string }[];
};

export function workflowSocketUrl(requestId: string, after = 0) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}${BASE}/ws/workflows/${requestId}?token=${encodeURIComponent(token ?? "")}&after=${after}`;
}

export type ApprovalRow = { id: string; decision: string; reason: string | null; decided_at: string; approver_name: string; purchase_order_id: string; po_number: string; total: string; currency: string; vendor_name: string; request_id: string; request_title: string | null };
export type HistoryRow = { from_state: string | null; to_state: string; reason: string | null; actor: string; created_at: string };
export type RunRow = { id: string; request_id: string; request_title: string | null; agent_name: string; status: string; error: string | null; duration_ms: number | null; tool_calls: number; started_at: string | null };
export type RunLogs = {
  run: AgentRunSummary;
  tool_calls: { id: string; tool_name: string; is_write: boolean; status: string; arguments: Record<string, unknown>; error: string | null; duration_ms: number | null; created_at: string }[];
  model_calls: { id: string; provider: string; model: string; purpose: string | null; latency_ms: number; input_tokens: number | null; output_tokens: number | null; attempt: number; fallback_used: boolean; success: boolean; error_type: string | null; created_at: string }[];
};
export type Intelligence = {
  models: { provider: string; model: string; calls: number; success_rate: number | null; fallback_calls: number; avg_latency_ms: number; p95_latency_ms: number; input_tokens: number; output_tokens: number }[];
  model_errors: { provider: string; error_type: string; count: number }[];
  tools: { tool: string; calls: number; success_rate: number | null; blocked: number; duplicates: number; avg_ms: number }[];
  agents: { agent: string; runs: number; success_rate: number | null; timeouts: number; avg_duration_ms: number }[];
};
export type AuditRow = { id: string; created_at: string; actor_type: string; actor: string; action: string; entity_type: string; entity_id: string | null; details: Record<string, unknown> };

export function formatMoney(amount: string | null, currency = "USD") {
  if (amount == null) return "—";
  return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(Number(amount));
}
