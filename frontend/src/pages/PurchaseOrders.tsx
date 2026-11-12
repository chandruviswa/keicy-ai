import { FileText, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, formatMoney, type PurchaseOrder } from "../api";
import { Badge, Card, EmptyState, ErrorState, Loading, PageHeader, StatusBadge, statusLabel, table } from "../components/ui";
import { useApi } from "../hooks";

const STATUSES = ["", "PENDING_APPROVAL", "APPROVED", "PAID", "DRAFT", "REJECTED", "CANCELLED"];

export default function PurchaseOrders() {
  const [status, setStatus] = useState("");
  const [open, setOpen] = useState<PurchaseOrder | null>(null);
  const pos = useApi(() => api.purchaseOrders(status || undefined), [status]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const total = (pos.data ?? []).reduce((sum, p) => sum + Number(p.total), 0);

  return (
    <>
      <PageHeader title="Purchase Orders" description="Orders drafted by the agents. Totals come from database prices, never from the model; only approvers can approve." />

      <div className="mb-4 flex gap-1 overflow-x-auto rounded-[var(--radius-control)] border border-line bg-white p-1" role="radiogroup" aria-label="Filter by status">
        {STATUSES.map((s) => (
          <button key={s || "all"} role="radio" aria-checked={status === s} onClick={() => { setStatus(s); setOpen(null); }}
            className={`shrink-0 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${status === s ? "bg-ink-900 text-white" : "text-ink-600 hover:bg-ink-50"}`}>
            {s ? statusLabel(s) : "All"}
          </button>
        ))}
      </div>

      <div className="grid gap-4 sm:gap-6 xl:grid-cols-[minmax(0,1fr)_420px] 2xl:grid-cols-[minmax(0,1fr)_480px]">
        <Card className="min-w-0 overflow-hidden">
          {pos.loading ? <Loading /> : pos.error ? <div className="p-4"><ErrorState message={pos.error} onRetry={pos.reload} /></div> : !pos.data?.length ? (
            <EmptyState icon={FileText} title="No purchase orders here" description="Run a request in the AI Workspace and the agents will draft one." />
          ) : (
            <>
              <div className={table.wrap}>
                <table className={table.table}>
                  <thead className={table.thead}>
                    <tr>
                      <th className={table.th}>Order</th>
                      <th className={`${table.th} hidden md:table-cell`}>Vendor</th>
                      <th className={`${table.th} text-right`}>Total</th>
                      <th className={table.th}>Status</th>
                      <th className={`${table.th} hidden lg:table-cell`}>Created</th>
                    </tr>
                  </thead>
                  <tbody className={table.tbody}>
                    {pos.data.map((p) => (
                      <tr key={p.id} onClick={() => setOpen(p)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setOpen(p)}
                        className={`cursor-pointer ${table.row} ${open?.id === p.id ? "bg-brand-50/70 hover:bg-brand-50/70" : ""}`} aria-selected={open?.id === p.id}>
                        <td className={table.td}>
                          <span className="font-medium text-ink-900 whitespace-nowrap">{p.po_number}</span>
                          <div className="text-xs text-ink-500 md:hidden">{p.vendor_name}</div>
                        </td>
                        <td className={`${table.td} hidden md:table-cell`}>{p.vendor_name}</td>
                        <td className={`${table.td} text-right font-medium tabular-nums`}>{formatMoney(p.total, p.currency)}</td>
                        <td className={table.td}><StatusBadge status={p.status} /></td>
                        <td className={`${table.td} hidden text-ink-500 lg:table-cell`}>{new Date(p.created_at).toLocaleDateString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="border-t border-line px-4 py-3 text-sm text-ink-500 sm:px-5">
                {pos.data.length} order{pos.data.length === 1 ? "" : "s"} · {formatMoney(String(total.toFixed(2)))} in total
              </p>
            </>
          )}
        </Card>

        {open ? (
          <aside className="fixed inset-0 z-40 overflow-y-auto bg-canvas p-4 sm:p-6 xl:static xl:z-auto xl:overflow-visible xl:bg-transparent xl:p-0">
            <Detail po={open} onClose={() => setOpen(null)} />
          </aside>
        ) : (
          <Card className="hidden h-fit p-6 text-sm text-ink-500 xl:block">Select an order to see its lines, totals and approval decisions.</Card>
        )}
      </div>
    </>
  );
}

function Detail({ po, onClose }: { po: PurchaseOrder; onClose: () => void }) {
  return (
    <Card className="p-5 xl:sticky xl:top-[88px]">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold whitespace-nowrap">{po.po_number}</h2>
          <p className="text-sm text-ink-500">{po.vendor_name}</p>
        </div>
        <button onClick={onClose} aria-label="Close" className="rounded-md p-1.5 text-ink-400 hover:bg-ink-50 hover:text-ink-700"><X size={18} /></button>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <StatusBadge status={po.status} />
        {po.request_status && <Badge>request: {statusLabel(po.request_status).toLowerCase()}</Badge>}
      </div>

      <table className="mt-5 w-full text-sm">
        <thead className="border-b border-line text-left text-xs text-ink-500">
          <tr><th className="pb-2 font-medium">Item</th><th className="pb-2 text-right font-medium">Qty</th><th className="hidden pb-2 text-right font-medium sm:table-cell">Unit</th><th className="pb-2 text-right font-medium">Line</th></tr>
        </thead>
        <tbody className="divide-y divide-line">
          {po.items.map((i) => (
            <tr key={i.id}>
              <td className="py-2 pr-2">{i.description}</td>
              <td className="py-2 text-right tabular-nums">{i.quantity}</td>
              <td className="hidden py-2 text-right tabular-nums sm:table-cell">{formatMoney(i.unit_price, po.currency)}</td>
              <td className="py-2 text-right tabular-nums">{formatMoney(i.line_total, po.currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <dl className="mt-3 grid gap-1.5 border-t border-line pt-3 text-sm">
        <div className="flex justify-between"><dt className="text-ink-500">Subtotal</dt><dd className="tabular-nums">{formatMoney(po.subtotal, po.currency)}</dd></div>
        <div className="flex justify-between"><dt className="text-ink-500">Tax</dt><dd className="tabular-nums">{formatMoney(po.tax_amount, po.currency)}</dd></div>
        <div className="flex justify-between text-base font-semibold"><dt>Total</dt><dd className="tabular-nums">{formatMoney(po.total, po.currency)}</dd></div>
      </dl>

      {po.approvals.length > 0 && (
        <div className="mt-5">
          <p className="mb-2 text-sm font-medium text-ink-700">Decisions</p>
          <ul className="grid gap-2 text-sm">
            {po.approvals.map((a, i) => (
              <li key={i} className="flex flex-wrap items-center gap-2">
                <Badge tone={a.decision === "APPROVED" ? "success" : "danger"}>{a.decision === "APPROVED" ? "Approved" : "Rejected"}</Badge>
                <span className="text-ink-500">{new Date(a.decided_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}</span>
                {a.reason && <span className="w-full text-ink-600">{a.reason}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      <Link to={`/workspace/${po.request_id}`} className="mt-5 inline-flex text-sm font-medium text-brand-600 hover:text-brand-700">Open the request</Link>
    </Card>
  );
}
