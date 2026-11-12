import {
  Bot, Check, CheckCircle2, Circle, CircleX, CreditCard, FileText, Loader2, Play, Receipt, RotateCcw, ShieldCheck, Sparkles, Wrench, X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";
import { api, formatMoney, type HistoryRow, type Offers, type PaymentRecord, type ProcurementRequest, type WorkflowEvent, type WorkflowStatus } from "../api";
import { useAuth } from "../auth";
import { PayPalCheckout } from "../components/PayPalCheckout";
import { Badge, Button, Card, ErrorState, Field, Loading, Panel, StatusBadge, inputClass, statusLabel, table, textareaClass } from "../components/ui";
import { useToast } from "../toast";
import { useWorkflowEvents } from "../useWorkflowEvents";

const PAYABLE = ["APPROVED", "PAYMENT_PENDING", "PAYMENT_FAILED"];
const PAST_APPROVAL = ["APPROVED", "PAYMENT_PENDING", "PAYMENT_PROCESSING", "PAYMENT_COMPLETED", "PAYMENT_FAILED", "RECONCILING", "COMPLETED"];
const PAID = ["PAYMENT_COMPLETED", "RECONCILING", "COMPLETED"];

type StepState = "idle" | "running" | "done" | "failed" | "waiting";

function agentStates(events: WorkflowEvent[]): Record<string, StepState> {
  // Only the latest execution counts (a request can be re-run after it needed attention).
  const start = events.map((e) => e.event_type).lastIndexOf("workflow.started");
  const out: Record<string, StepState> = {};
  for (const e of events.slice(Math.max(0, start))) {
    const agent = String(e.payload.agent ?? "");
    if (e.event_type === "agent.started") out[agent] = "running";
    if (e.event_type === "agent.completed") out[agent] = "done";
    if (e.event_type === "agent.failed") out[agent] = "failed";
  }
  return out;
}

/* ---------- the routing slip ---------- */

type Station = { key: string; label: string; state: StepState; note: string };

function stations(current: string, agents: Record<string, StepState>, history: HistoryRow[]): Station[] {
  const note = (s: StepState, waiting = "Waiting") =>
    ({ idle: "Not started", running: "Working…", done: "Done", failed: "Stopped", waiting })[s];
  const agent = (key: string, label: string): Station => ({ key, label, state: agents[key] ?? "idle", note: note(agents[key] ?? "idle") });
  const lastFrom = history.at(-1)?.from_state;

  const approval: StepState = PAST_APPROVAL.includes(current) ? "done"
    : current === "AWAITING_APPROVAL" ? "waiting"
    : current === "REJECTED" && lastFrom === "AWAITING_APPROVAL" ? "failed" : "idle";
  const payment: StepState = PAID.includes(current) ? "done"
    : current === "PAYMENT_FAILED" ? "failed"
    : ["APPROVED", "PAYMENT_PENDING"].includes(current) ? "waiting"
    : current === "PAYMENT_PROCESSING" ? "running" : "idle";
  const recon = current === "COMPLETED" ? "done" : agents.reconciliation ?? "idle";

  return [
    agent("intent", "Understand"),
    agent("research", "Research"),
    agent("evaluation", "Compare"),
    agent("procurement", "Purchase order"),
    agent("finance", "Finance check"),
    { key: "approval", label: "Approval", state: approval, note: approval === "waiting" ? "Needs a person" : approval === "failed" ? "Rejected" : note(approval) },
    { key: "payment", label: "Payment", state: payment, note: payment === "waiting" ? "Ready to pay" : note(payment) },
    { key: "reconciled", label: "Reconciled", state: recon, note: recon === "done" ? "Receipt issued" : note(recon) },
  ];
}

function RoutingSlip({ items }: { items: Station[] }) {
  return (
    <Card className="mb-6 px-4 py-5 sm:px-6">
      <ol className="grid grid-cols-4 gap-y-6 sm:grid-cols-8" aria-label="Request progress">
        {items.map((s, i) => {
          const icon = s.state === "done" ? <Check size={15} strokeWidth={3} />
            : s.state === "failed" ? <X size={15} strokeWidth={3} />
            : s.state === "running" ? <Loader2 size={15} className="animate-spin" />
            : <span className="text-xs font-semibold">{i + 1}</span>;
          const dot = {
            done: "border-ink-900 bg-ink-900 text-white",
            failed: "border-stamp-600 bg-stamp-600 text-white",
            running: "border-brand-500 bg-brand-50 text-brand-600 station-active",
            waiting: "border-canary-500 bg-canary-500 text-ink-950 station-active",
            idle: "border-ink-200 bg-white text-ink-400",
          }[s.state];
          const lineDone = s.state === "done";
          return (
            <li key={s.key} className="relative flex flex-col items-center text-center" aria-current={s.state === "running" || s.state === "waiting" ? "step" : undefined}>
              {i < items.length - 1 && (
                <span aria-hidden className={`absolute top-4 left-[calc(50%+18px)] h-0.5 w-[calc(100%-36px)] ${i % 4 === 3 ? "hidden sm:block" : ""} ${lineDone ? "bg-ink-900" : "bg-line"}`} />
              )}
              <span className={`relative z-10 grid h-8 w-8 place-items-center rounded-full border-2 ${dot}`}>{icon}</span>
              <span className={`mt-2 text-[13px] leading-tight font-semibold ${s.state === "idle" ? "text-ink-400" : "text-ink-900"}`}>{s.label}</span>
              <span className={`mt-0.5 text-xs ${s.state === "failed" ? "text-stamp-600" : s.state === "waiting" ? "text-canary-700" : "text-ink-400"}`}>{s.note}</span>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}

/* ---------- live activity ---------- */

function describe(e: WorkflowEvent): { icon: typeof Bot; text: string; tone: string } {
  const p = e.payload as Record<string, string | number | null>;
  switch (e.event_type) {
    case "workflow.started":
      return { icon: Play, text: "Workflow started", tone: "text-brand-600" };
    case "workflow.state_changed":
      return { icon: Circle, text: `${statusLabel(String(p.from))} → ${statusLabel(String(p.to))}${p.reason ? ` · ${p.reason}` : ""}`, tone: "text-ink-500" };
    case "agent.started":
      return { icon: Bot, text: `${p.agent} agent started`, tone: "text-brand-600" };
    case "agent.completed":
      return { icon: CheckCircle2, text: `${p.agent} agent finished in ${((Number(p.duration_ms) || 0) / 1000).toFixed(1)}s · ${p.tool_calls} tool call(s)`, tone: "text-ok-700" };
    case "agent.failed":
      return { icon: CircleX, text: `${p.agent} agent ${String(p.status).toLowerCase()}: ${p.error}`, tone: "text-stamp-600" };
    case "agent.tool_called":
      return { icon: Wrench, text: `${p.tool} · ${p.duration_ms} ms${p.status !== "SUCCEEDED" ? ` · ${String(p.status).toLowerCase()}` : ""}${p.error ? ` · ${p.error}` : ""}`, tone: p.status === "SUCCEEDED" || p.status === "DUPLICATE" ? "text-ink-400" : "text-stamp-600" };
    case "payment.order_created":
      return { icon: CreditCard, text: `PayPal order ${p.paypal_order_id} created · ${formatMoney(String(p.amount), String(p.currency))}`, tone: "text-brand-600" };
    case "payment.completed":
      return { icon: CheckCircle2, text: `Payment captured · ${p.capture_id} · ${formatMoney(String(p.amount), String(p.currency))}`, tone: "text-ok-700" };
    case "payment.failed":
      return { icon: CircleX, text: `Payment failed: ${p.reason}`, tone: "text-stamp-600" };
    case "payment.cancelled":
      return { icon: X, text: "Buyer closed the PayPal checkout — nothing was charged", tone: "text-ink-500" };
    case "approval.required":
      return { icon: ShieldCheck, text: `Approval required for ${p.po_number} · ${formatMoney(String(p.total), String(p.currency))}`, tone: "text-canary-700" };
    default:
      return { icon: Circle, text: e.event_type.replace("workflow.", "Workflow "), tone: "text-ink-600" };
  }
}

function LiveActivity({ events, connected, idleHint }: { events: WorkflowEvent[]; connected: boolean; idleHint: string }) {
  const [showTools, setShowTools] = useState(true);
  const list = useRef<HTMLOListElement>(null);
  const visible = showTools ? events : events.filter((e) => e.event_type !== "agent.tool_called");
  useEffect(() => {
    // Follow new events inside the feed only — never scroll the page itself.
    const el = list.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events.length, showTools]);

  return (
    <Panel
      title="Live activity"
      flush
      actions={
        <>
          <label className="flex items-center gap-1.5 text-xs text-ink-500">
            <input type="checkbox" checked={showTools} onChange={(e) => setShowTools(e.target.checked)} className="accent-brand-600" /> Tool calls
          </label>
          <span title={connected ? "Live updates connected" : "Reconnecting…"} className="flex items-center gap-1.5 text-xs text-ink-500">
            <span className={`h-2 w-2 rounded-full ${connected ? "bg-ok-600" : "bg-ink-300"}`} /> {connected ? "Live" : "Offline"}
          </span>
        </>
      }
    >
      {events.length === 0 ? (
        <p className="p-5 text-sm text-ink-500">{idleHint}</p>
      ) : (
        <ol ref={list} className="max-h-[min(70vh,640px)] space-y-2 overflow-y-auto p-4 text-[13px] sm:px-5">
          {visible.map((e) => {
            const d = describe(e);
            const tool = e.event_type === "agent.tool_called";
            return (
              <li key={e.seq} className={`flex gap-2 ${d.tone} ${tool ? "pl-5 text-xs" : ""}`}>
                <d.icon size={tool ? 12 : 14} className="mt-0.5 shrink-0" />
                <span className="min-w-0 flex-1 break-words">{d.text}</span>
                <time className="shrink-0 pl-2 text-xs text-ink-400">{new Date(e.created_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" })}</time>
              </li>
            );
          })}
        </ol>
      )}
    </Panel>
  );
}

/* ---------- page ---------- */

export default function RequestDetail() {
  const { id } = useParams<{ id: string }>();
  const [request, setRequest] = useState<ProcurementRequest | null>(null);
  const [status, setStatus] = useState<WorkflowStatus | null>(null);
  const [offers, setOffers] = useState<Offers | null>(null);
  const [payments, setPayments] = useState<PaymentRecord[]>([]);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [poStatus, setPoStatus] = useState<string | null>(null);
  const { user, can } = useAuth();
  const toast = useToast();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const refreshTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  const refresh = useCallback(async () => {
    if (!id) return;
    try {
      const [r, s, o, p, h] = await Promise.all([api.getRequest(id), api.status(id), api.offers(id), api.payments(id), api.history(id)]);
      setRequest(r);
      setStatus(s);
      setOffers(o);
      setPayments(p);
      setHistory(h);
      // The agents' output is a snapshot from when the order was drafted; show the order's status now.
      setPoStatus(s.purchase_order_id ? (await api.purchaseOrder(s.purchase_order_id)).status : null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [id]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const live = useWorkflowEvents(id, (e) => {
    if (e.event_type !== "agent.tool_called") {
      clearTimeout(refreshTimer.current);
      refreshTimer.current = setTimeout(refresh, 250);
    }
  });

  async function act(fn: () => Promise<unknown>, success?: string) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      if (success) toast(success, "success");
      await refresh();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      toast(msg, "error");
    } finally {
      setBusy(false);
    }
  }

  if (!request || !status) return error ? <ErrorState message={error} onRetry={refresh} /> : <Loading />;

  const current = live.status ?? status.status;
  const agents = agentStates(live.events);
  const running = status.is_running || Object.values(agents).includes("running");
  const isOwner = user?.id === request.requester_id;
  const canRun = ["CREATED", "FAILED"].includes(current) && !running && isOwner;
  const canCancel = isOwner && status.allowed_next.includes("CANCELLED") && !["APPROVED", "PAYMENT_PENDING"].includes(current);
  const canDecide = can("approver") && !isOwner;

  return (
    <>
      <div className="mb-5 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <StatusBadge status={current} />
            {running && <span className="flex items-center gap-1.5 text-xs text-brand-600"><Loader2 size={12} className="animate-spin" /> Agents working</span>}
          </div>
          <h1 className="text-2xl font-semibold tracking-tight sm:text-[1.75rem]">{request.title ?? "Untitled request"}</h1>
          <p className="mt-1.5 max-w-3xl text-[15px] leading-relaxed text-ink-600">“{request.raw_text}”</p>
          <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-sm">
            <div className="flex gap-1.5"><dt className="text-ink-400">Budget</dt><dd className="font-medium tabular-nums">{formatMoney(request.budget, request.currency)}</dd></div>
            <div className="flex gap-1.5"><dt className="text-ink-400">Needed by</dt><dd className="font-medium">{request.deadline ? new Date(request.deadline + "T00:00").toLocaleDateString() : "No deadline"}</dd></div>
            <div className="flex gap-1.5"><dt className="text-ink-400">Submitted</dt><dd className="font-medium">{new Date(request.created_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}</dd></div>
          </dl>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {canRun && (
            <Button disabled={busy} onClick={() => act(() => api.execute(request.id), "Agents started")}>
              {current === "FAILED" ? <RotateCcw size={15} /> : <Play size={15} />} {current === "FAILED" ? "Re-run agents" : "Run agents"}
            </Button>
          )}
          {canCancel && (
            <Button variant="secondary" disabled={busy} onClick={() => act(() => api.cancel(request.id), "Request cancelled")}>
              <X size={15} /> Cancel request
            </Button>
          )}
        </div>
      </div>

      <RoutingSlip items={stations(current, agents, history)} />

      {error && <div className="mb-4"><ErrorState message={error} /></div>}
      {current === "FAILED" && status.last_reason && (
        <Clarify request={request} status={status} busy={busy} canEdit={isOwner}
          onSave={(body) => act(async () => { await api.updateRequest(request.id, body); await api.execute(request.id); }, "Request updated — agents restarted")} />
      )}
      {current === "REJECTED" && status.last_reason && (
        <div role="alert" className="mb-6 flex gap-3 rounded-[var(--radius-panel)] border border-stamp-100 bg-stamp-50 p-4 text-sm text-stamp-700">
          <CircleX size={18} className="mt-0.5 shrink-0" />
          <p><strong>Rejected:</strong> {status.last_reason}</p>
        </div>
      )}

      <div className="grid gap-4 sm:gap-6 xl:grid-cols-12">
        <div className="grid min-w-0 content-start gap-4 sm:gap-6 xl:col-span-8">
          {offers?.requirement && <Requirements offers={offers} />}
          {offers && offers.ranking.length > 0 && <Ranking offers={offers} />}
          {offers?.purchase_order && <PurchaseOrderCard offers={offers} poStatus={poStatus} current={current} busy={busy} act={act} canDecide={canDecide} />}
          {offers?.purchase_order && (PAYABLE.includes(current) || payments.length > 0) && (
            <PaymentCard offers={offers} current={current} payments={payments} canPay={isOwner} onFinished={refresh} />
          )}
          {!offers?.requirement && !running && current === "CREATED" && (
            <Card className="p-6 text-sm text-ink-500">This request hasn't been run yet. Run the agents to get a recommendation.</Card>
          )}
        </div>
        <div className="grid min-w-0 content-start gap-4 sm:gap-6 xl:sticky xl:top-[88px] xl:col-span-4 xl:self-start">
          <LiveActivity events={live.events} connected={live.connected} idleHint={canRun ? "Run the agents to watch their work here in real time." : "Waiting for activity…"} />
          {history.length > 0 && <HistoryCard history={history} />}
        </div>
      </div>
    </>
  );
}

/* ---------- sections ---------- */

function Clarify({ request, status, busy, canEdit, onSave }: { request: ProcurementRequest; status: WorkflowStatus; busy: boolean; canEdit: boolean; onSave: (b: { raw_text?: string; budget?: string }) => void }) {
  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const raw = String(f.get("raw_text") ?? "").trim();
    const budget = String(f.get("budget") ?? "").trim();
    onSave({ ...(raw && raw !== request.raw_text ? { raw_text: raw } : {}), ...(budget ? { budget } : {}) });
  }
  const questions = status.clarification_questions;
  return (
    <Card className="relative mb-6 overflow-hidden border-canary-500/50 p-5 sm:p-6">
      <span aria-hidden className="absolute inset-y-0 left-0 w-1 bg-canary-500" />
      <h2 className="font-semibold text-ink-900">{questions.length ? "The agents need a bit more information" : "The last run stopped"}</h2>
      {questions.length > 0 ? (
        <ul className="mt-2 grid gap-1 text-sm text-ink-700">{questions.map((q) => <li key={q} className="flex gap-2"><span className="text-canary-700">?</span>{q}</li>)}</ul>
      ) : (
        <p className="mt-1 text-sm text-ink-600">{status.last_reason}</p>
      )}
      {canEdit && (
        <form onSubmit={submit} className="mt-4 grid gap-3 lg:grid-cols-[minmax(0,1fr)_12rem_auto] lg:items-end">
          <Field label="Request">
            <textarea name="raw_text" defaultValue={request.raw_text} rows={2} className={textareaClass} />
          </Field>
          <Field label="Budget">
            <input name="budget" type="number" min="0.01" step="0.01" inputMode="decimal" defaultValue={request.budget ?? ""} className={inputClass} />
          </Field>
          <Button disabled={busy}><RotateCcw size={15} /> Update & re-run</Button>
        </form>
      )}
    </Card>
  );
}

function humanSpec(key: string, value: unknown): string {
  const m = key.match(/^(.*?)(?:_(min|max))?$/)!;
  const parts = m[1].split("_");
  const units = ["gb", "tb", "in", "kg", "w", "h", "cm", "mm"];
  const unit = units.includes(parts.at(-1)!) ? parts.pop()!.toUpperCase().replace("IN", "in").replace("KG", "kg").replace("CM", "cm").replace("MM", "mm") : "";
  const name = parts.join(" ");
  const label = name.length <= 3 ? name.toUpperCase() : name.charAt(0).toUpperCase() + name.slice(1);
  const op = m[2] === "min" ? "≥ " : m[2] === "max" ? "≤ " : ": ";
  return `${label} ${op}${String(value)}${unit ? ` ${unit}` : ""}`.replace(" : ", ": ");
}

function Requirements({ offers }: { offers: Offers }) {
  const r = offers.requirement!;
  return (
    <Panel title={<span className="flex items-center gap-2"><Sparkles size={16} className="text-brand-600" /> What the agents understood</span>}>
      <ul className="grid gap-3">
        {r.items.map((it, i) => (
          <li key={i} className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[15px] font-semibold">{it.quantity} × {it.product_name}</span>
              {Object.entries(it.specifications).map(([k, v]) => <Badge key={k} tone="info">{humanSpec(k, v)}</Badge>)}
            </div>
            {offers.items[i] && (
              <span className="text-sm text-ink-500">
                Matched {offers.items[i].products.map((p) => p.sku).join(", ")} · {offers.items[i].eligible_quotes} eligible quote{offers.items[i].eligible_quotes === 1 ? "" : "s"}
              </span>
            )}
          </li>
        ))}
      </ul>
      <dl className="mt-4 flex flex-wrap gap-x-6 gap-y-1 border-t border-line pt-3 text-sm">
        <div className="flex gap-1.5"><dt className="text-ink-400">Budget</dt><dd className="font-medium tabular-nums">{formatMoney(r.budget, r.currency)}</dd></div>
        <div className="flex gap-1.5"><dt className="text-ink-400">Deadline</dt><dd className="font-medium">{r.deadline ? new Date(r.deadline + "T00:00").toLocaleDateString() : "None"}</dd></div>
      </dl>
    </Panel>
  );
}

function Ranking({ offers }: { offers: Offers }) {
  return (
    <Panel title="Vendor comparison" description="Scored by rules on price (45%), delivery (25%), vendor rating (15%) and budget fit (15%)." flush>
      {offers.explanation && (
        <div className="border-b border-line px-4 py-4 sm:px-5">
          <p className="max-w-3xl text-[15px] leading-relaxed text-ink-700">{offers.explanation.summary}</p>
          {offers.explanation.risks.length > 0 && (
            <p className="mt-2 flex items-start gap-2 text-sm text-canary-700"><ShieldCheck size={15} className="mt-0.5 shrink-0" /> {offers.explanation.risks.join(" · ")}</p>
          )}
          <p className="mt-2 text-xs text-ink-400">Explanation written by {offers.explanation_source?.replace("llm:", "") ?? "—"}; the ranking itself is computed, not generated.</p>
        </div>
      )}
      <div className={table.wrap}>
        <table className={table.table}>
          <thead className={table.thead}>
            <tr>
              <th className={table.th}>Vendor</th>
              <th className={`${table.th} text-right`}>Total</th>
              <th className={`${table.th} hidden text-right sm:table-cell`}>Delivery</th>
              <th className={`${table.th} hidden text-right md:table-cell`}>Rating</th>
              <th className={`${table.th} text-right`}>Score</th>
            </tr>
          </thead>
          <tbody className={table.tbody}>
            {offers.ranking.map((c) => (
              <tr key={c.vendor_id} className={c.rank === 1 ? "bg-ok-50/50" : ""}>
                <td className={table.td}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-ink-900">{c.vendor_name}</span>
                    {c.rank === 1 && <Badge tone="success">recommended</Badge>}
                    {!c.within_budget && <Badge tone="warning">over budget</Badge>}
                  </div>
                  <div className="mt-0.5 text-xs text-ink-500">{c.lines.map((l) => `${l.quantity} × ${l.sku}`).join(", ")}<span className="sm:hidden"> · {c.lead_time_days} days</span></div>
                </td>
                <td className={`${table.td} text-right font-medium tabular-nums`}>{formatMoney(c.total, c.currency)}</td>
                <td className={`${table.td} hidden text-right tabular-nums sm:table-cell`}>{c.lead_time_days} days</td>
                <td className={`${table.td} hidden text-right tabular-nums md:table-cell`}>{c.vendor_rating}</td>
                <td className={`${table.td} text-right`}>
                  <div className="flex items-center justify-end gap-2">
                    <span className="hidden h-1.5 w-16 overflow-hidden rounded-full bg-ink-100 lg:block" aria-hidden>
                      <span className={`block h-full rounded-full ${c.rank === 1 ? "bg-ok-600" : "bg-ink-300"}`} style={{ width: `${Number(c.score)}%` }} />
                    </span>
                    <span className="font-semibold tabular-nums">{c.score}</span>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {offers.excluded.length > 0 && <p className="border-t border-line px-4 py-3 text-xs text-ink-500 sm:px-5">Not considered: {offers.excluded.join(" · ")}</p>}
    </Panel>
  );
}

function PurchaseOrderCard({ offers, poStatus, current, busy, act, canDecide }: { offers: Offers; poStatus: string | null; current: string; busy: boolean; act: (fn: () => Promise<unknown>, success?: string) => void; canDecide: boolean }) {
  const po = offers.purchase_order!;
  const [reason, setReason] = useState("");
  return (
    <Panel title={<span className="flex items-center gap-2"><FileText size={16} className="text-ink-500" /> Purchase order {po.po_number}</span>} actions={<StatusBadge status={poStatus ?? po.status} />}>
      <p className="max-w-3xl text-sm text-ink-600">{offers.po_summary}</p>
      <dl className="mt-4 grid grid-cols-3 gap-3 rounded-[var(--radius-control)] bg-ink-50 p-3 sm:p-4">
        <div><dt className="text-xs text-ink-500">Subtotal</dt><dd className="mt-0.5 font-medium tabular-nums">{formatMoney(po.subtotal, po.currency)}</dd></div>
        <div><dt className="text-xs text-ink-500">Tax</dt><dd className="mt-0.5 font-medium tabular-nums">{formatMoney(po.tax_amount, po.currency)}</dd></div>
        <div><dt className="text-xs text-ink-500">Total</dt><dd className="mt-0.5 text-lg font-semibold tabular-nums">{formatMoney(po.total, po.currency)}</dd></div>
      </dl>
      {offers.finance_report && (
        <div className="mt-4">
          <p className="mb-2 text-sm font-medium text-ink-700">Finance checks</p>
          <ul className="grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2 lg:grid-cols-4">
            {offers.finance_report.checks.map((c) => (
              <li key={c.name} className={`flex items-center gap-1.5 ${c.passed ? "text-ink-700" : "text-stamp-700"}`} title={c.detail}>
                {c.passed ? <Check size={14} className="shrink-0 text-ok-600" /> : <X size={14} className="shrink-0" />} {c.name.replaceAll("_", " ")}
              </li>
            ))}
          </ul>
        </div>
      )}
      {current === "AWAITING_APPROVAL" && !canDecide && (
        <p className="mt-5 flex items-center gap-2 border-t border-line pt-4 text-sm text-canary-700"><ShieldCheck size={15} /> Waiting for an approver to review this purchase order.</p>
      )}
      {current === "AWAITING_APPROVAL" && canDecide && (
        <div className="mt-5 flex flex-col gap-2 border-t border-line pt-4 sm:flex-row">
          <Button disabled={busy} onClick={() => act(() => api.approve(po.purchase_order_id), `${po.po_number} approved`)}><Check size={15} /> Approve</Button>
          <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason for rejecting" aria-label="Rejection reason" className={`${inputClass} sm:max-w-sm sm:flex-1`} />
          <Button variant="danger" disabled={busy || reason.trim().length < 3} onClick={() => act(() => api.reject(po.purchase_order_id, reason.trim()), `${po.po_number} rejected`)}><X size={15} /> Reject</Button>
        </div>
      )}
    </Panel>
  );
}

function PaymentCard({ offers, current, payments, canPay, onFinished }: { offers: Offers; current: string; payments: PaymentRecord[]; canPay: boolean; onFinished: () => void }) {
  const po = offers.purchase_order!;
  const receipt = payments.find((p) => p.status === "COMPLETED")?.receipt;
  return (
    <Panel title={<span className="flex items-center gap-2"><CreditCard size={16} className="text-ink-500" /> Payment</span>}>
      {PAYABLE.includes(current) && !canPay && <p className="text-sm text-ink-600">Approved — waiting for the requester to pay with PayPal.</p>}
      {PAYABLE.includes(current) && canPay && (
        <PayPalCheckout purchaseOrderId={po.purchase_order_id} amount={po.total} currency={po.currency} poNumber={po.po_number} onFinished={() => onFinished()} />
      )}
      {current === "PAYMENT_PROCESSING" && <p className="flex items-center gap-2 text-sm text-ink-600"><Loader2 size={15} className="animate-spin" /> PayPal is still processing the capture — this page updates on its own.</p>}
      {(current === "PAYMENT_COMPLETED" || current === "RECONCILING") && <p className="flex items-center gap-2 text-sm text-ok-700"><Loader2 size={15} className="animate-spin" /> Payment captured — checking it against PayPal's records…</p>}

      {receipt && (
        <div className="rounded-[var(--radius-control)] border border-ok-100 bg-ok-50/60 p-4 sm:p-5">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <span className="flex items-center gap-2 font-semibold text-ok-700"><Receipt size={16} /> Receipt {receipt.receipt_number}</span>
            <span className="text-xl font-semibold tabular-nums">{formatMoney(receipt.amount, receipt.currency)}</span>
          </div>
          <table className="mt-3 w-full text-sm">
            <tbody className="divide-y divide-ok-100">
              {receipt.lines.map((l, i) => (
                <tr key={i}><td className="py-1.5">{l.quantity} × {l.description}</td><td className="py-1.5 text-right tabular-nums">{formatMoney(l.line_total, receipt.currency)}</td></tr>
              ))}
              <tr><td className="py-1.5 text-ink-500">Tax</td><td className="py-1.5 text-right tabular-nums">{formatMoney(receipt.tax_amount, receipt.currency)}</td></tr>
            </tbody>
          </table>
          <dl className="mt-3 grid gap-x-6 gap-y-1 text-xs text-ink-600 sm:grid-cols-2">
            <div>Paid {receipt.paid_at ? new Date(receipt.paid_at).toLocaleString() : "—"}</div>
            <div>PayPal order <span className="font-medium text-ink-800">{receipt.paypal_order_id}</span></div>
            <div>Purchase order <span className="font-medium text-ink-800">{receipt.po_number}</span></div>
            <div>Capture <span className="font-medium text-ink-800">{receipt.capture_id}</span></div>
          </dl>
        </div>
      )}

      {payments.length > 0 && (
        <ul className="mt-4 grid gap-1.5 text-xs text-ink-500">
          {payments.map((p) => (
            <li key={p.id} className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span>Attempt {p.attempt}</span>
              <StatusBadge status={p.status} />
              <span className="tabular-nums">{formatMoney(p.amount, p.currency)}</span>
              {p.paypal_orders[0] && <span>{p.paypal_orders[0].paypal_order_id}</span>}
              {p.failure_reason && <span className="text-stamp-600">· {p.failure_reason}</span>}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function HistoryCard({ history }: { history: HistoryRow[] }) {
  return (
    <Panel title="History" description="Every status change, who made it and why.">
      <ol className="relative grid gap-3 text-sm">
        {history.map((h, i) => (
          <li key={i} className="relative flex gap-3">
            {i < history.length - 1 && <span aria-hidden className="absolute top-4 bottom-[-12px] left-[3.5px] w-px bg-line" />}
            <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-ink-300" />
            <div className="min-w-0">
              <p className="font-medium text-ink-900">{statusLabel(h.to_state)}</p>
              <p className="text-xs text-ink-500">{h.actor} · {new Date(h.created_at).toLocaleString([], { dateStyle: "short", timeStyle: "short" })}</p>
              {h.reason && <p className="mt-0.5 text-xs break-words text-ink-500">{h.reason}</p>}
            </div>
          </li>
        ))}
      </ol>
    </Panel>
  );
}
