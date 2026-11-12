import { CreditCard, Receipt as ReceiptIcon, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, formatMoney, type PaymentRecord } from "../api";
import { Card, EmptyState, ErrorState, Loading, PageHeader, Panel, Stat, StatusBadge, table } from "../components/ui";
import { useApi } from "../hooks";

export default function Payments() {
  const payments = useApi(() => api.payments());
  const awaiting = useApi(() => api.purchaseOrders("APPROVED"));
  const [receipt, setReceipt] = useState<PaymentRecord | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setReceipt(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const rows = payments.data ?? [];
  const paid = rows.filter((p) => p.status === "COMPLETED");
  const paidTotal = paid.reduce((s, p) => s + Number(p.amount), 0);
  const failed = rows.filter((p) => p.status === "FAILED").length;

  return (
    <>
      <PageHeader title="Payment Center" description="Every PayPal payment for an approved order — each attempt, its PayPal references and the receipt once it's reconciled." />

      <div className="mb-6 grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
        <Stat label="Paid" value={formatMoney(paidTotal.toFixed(2))} hint={`${paid.length} payment${paid.length === 1 ? "" : "s"} reconciled`} />
        <Stat label="Ready to pay" value={awaiting.data?.length ?? "—"} attention={!!awaiting.data?.length} />
        <Stat label="Attempts" value={rows.length} />
        <Stat label="Failed or declined" value={failed} />
      </div>

      {awaiting.data && awaiting.data.length > 0 && (
        <Panel title="Ready to pay" description="Approved orders waiting for the requester's PayPal checkout." flush className="mb-6">
          <ul className="divide-y divide-line">
            {awaiting.data.map((p) => (
              <li key={p.id} className="flex flex-col gap-2 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between sm:px-5">
                <span><span className="font-medium text-ink-900 whitespace-nowrap">{p.po_number}</span> <span className="text-ink-500">· {p.vendor_name}</span></span>
                <span className="flex items-center gap-4">
                  <span className="font-semibold tabular-nums">{formatMoney(p.total, p.currency)}</span>
                  <Link to={`/workspace/${p.request_id}`} className="rounded-full bg-[#ffc439] px-4 py-1.5 text-xs font-bold text-[#003087] hover:brightness-95">Pay with PayPal</Link>
                </span>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <Panel title="Payment history" flush>
        {payments.loading ? <Loading /> : payments.error ? <div className="p-4"><ErrorState message={payments.error} onRetry={payments.reload} /></div> : !rows.length ? (
          <EmptyState icon={CreditCard} title="No payments yet" description="Payments appear here once an approved order is checked out with PayPal." />
        ) : (
          <div className={table.wrap}>
            <table className={table.table}>
              <thead className={table.thead}>
                <tr>
                  <th className={table.th}>Order</th>
                  <th className={`${table.th} text-right`}>Amount</th>
                  <th className={`${table.th} hidden sm:table-cell`}>Status</th>
                  <th className={`${table.th} hidden lg:table-cell`}>PayPal order</th>
                  <th className={`${table.th} hidden xl:table-cell`}>Capture</th>
                  <th className={`${table.th} hidden md:table-cell`}>When</th>
                  <th className={`${table.th} text-right`}><span className="sr-only">Receipt</span></th>
                </tr>
              </thead>
              <tbody className={table.tbody}>
                {rows.map((p) => (
                  <tr key={p.id} className={table.row}>
                    <td className={table.td}>
                      <Link to={`/workspace/${p.request_id}`} className="font-medium text-ink-900 hover:text-brand-600 whitespace-nowrap">{p.po_number}</Link>
                      <div className="max-w-[16rem] truncate text-xs text-ink-500">{p.request_title ?? "Untitled request"} · attempt {p.attempt}</div>
                      <div className="mt-1.5 sm:hidden"><StatusBadge status={p.status} /></div>
                    </td>
                    <td className={`${table.td} text-right font-medium tabular-nums`}>{formatMoney(p.amount, p.currency)}</td>
                    <td className={`${table.td} hidden sm:table-cell`}>
                      <StatusBadge status={p.status} />
                      {p.failure_reason && <div className="mt-1 max-w-[18rem] text-xs text-stamp-600">{p.failure_reason}</div>}
                    </td>
                    <td className={`${table.td} hidden text-ink-600 lg:table-cell`}>{p.paypal_orders[0]?.paypal_order_id ?? "—"}</td>
                    <td className={`${table.td} hidden text-ink-600 xl:table-cell`}>{p.captures[0]?.capture_id ?? "—"}</td>
                    <td className={`${table.td} hidden text-ink-500 md:table-cell`}>{new Date(p.created_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}</td>
                    <td className={`${table.td} text-right`}>
                      {p.receipt && (
                        <button onClick={() => setReceipt(p)} className="inline-flex items-center gap-1 text-sm font-medium text-brand-600 hover:text-brand-700">
                          <ReceiptIcon size={14} /> <span className="hidden sm:inline">Receipt</span><span className="sr-only sm:hidden">Receipt</span>
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {receipt?.receipt && (
        <div className="fixed inset-0 z-40 grid place-items-center bg-ink-950/40 px-4" onClick={() => setReceipt(null)} role="dialog" aria-modal="true" aria-label="Receipt">
          <Card className="w-full max-w-md p-6">
            <div onClick={(e) => e.stopPropagation()}>
              <div className="flex items-start justify-between">
                <div>
                  <p className="text-sm text-ink-500">Receipt</p>
                  <h2 className="text-lg font-semibold">{receipt.receipt.receipt_number}</h2>
                </div>
                <button onClick={() => setReceipt(null)} aria-label="Close" className="rounded-md p-1.5 text-ink-400 hover:bg-ink-50 hover:text-ink-700"><X size={18} /></button>
              </div>
              <table className="mt-4 w-full text-sm">
                <tbody className="divide-y divide-line">
                  {receipt.receipt.lines.map((l, i) => <tr key={i}><td className="py-2">{l.quantity} × {l.description}</td><td className="py-2 text-right tabular-nums">{formatMoney(l.line_total, receipt.receipt!.currency)}</td></tr>)}
                  <tr><td className="py-2 text-ink-500">Tax</td><td className="py-2 text-right tabular-nums">{formatMoney(receipt.receipt.tax_amount, receipt.receipt.currency)}</td></tr>
                  <tr className="text-base font-semibold"><td className="py-2">Paid</td><td className="py-2 text-right tabular-nums">{formatMoney(receipt.receipt.amount, receipt.receipt.currency)}</td></tr>
                </tbody>
              </table>
              <dl className="mt-4 grid gap-1 text-xs text-ink-600">
                <div>Purchase order <span className="font-medium text-ink-900">{receipt.receipt.po_number}</span></div>
                <div>Paid {receipt.receipt.paid_at ? new Date(receipt.receipt.paid_at).toLocaleString() : "—"}</div>
                <div>PayPal order <span className="font-medium text-ink-900">{receipt.receipt.paypal_order_id}</span></div>
                <div>Capture <span className="font-medium text-ink-900">{receipt.receipt.capture_id}</span></div>
              </dl>
            </div>
          </Card>
        </div>
      )}
    </>
  );
}
