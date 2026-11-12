import { BadgeCheck, Check, ShieldAlert, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, formatMoney, type Offers, type PurchaseOrder } from "../api";
import { Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader, Panel, inputClass, table } from "../components/ui";
import { useApi } from "../hooks";
import { useToast } from "../toast";

export default function Approvals() {
  const pending = useApi(() => api.purchaseOrders("PENDING_APPROVAL"));
  const history = useApi(api.approvals);
  const [selected, setSelected] = useState<PurchaseOrder | null>(null);

  useEffect(() => {
    if (pending.data && !selected) setSelected(pending.data[0] ?? null);
  }, [pending.data, selected]);

  async function decided() {
    setSelected(null);
    await Promise.all([pending.reload(), history.reload()]);
  }

  const queueTotal = (pending.data ?? []).reduce((s, p) => s + Number(p.total), 0);

  return (
    <>
      <PageHeader
        title="Approval Center"
        description="Nothing is bought until a person approves it. Review what the agents recommend, check the budget, then decide."
      />
      <div className="grid gap-4 sm:gap-6 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)] 2xl:grid-cols-[minmax(0,26rem)_minmax(0,1fr)]">
        <Panel title="Waiting for you" flush className="h-fit"
          actions={pending.data?.length ? <span className="text-sm text-ink-500 tabular-nums">{pending.data.length} · {formatMoney(queueTotal.toFixed(2))}</span> : null}>
          {pending.loading ? <Loading /> : pending.error ? <div className="p-4"><ErrorState message={pending.error} onRetry={pending.reload} /></div> : !pending.data?.length ? (
            <EmptyState icon={BadgeCheck} title="You're all caught up" description="New purchase orders appear here when the Finance Agent sends them for approval." />
          ) : (
            <ul className="divide-y divide-line">
              {pending.data.map((p) => (
                <li key={p.id}>
                  <button onClick={() => setSelected(p)} aria-current={selected?.id === p.id}
                    className={`relative flex w-full items-center justify-between gap-3 px-4 py-3.5 text-left text-sm transition-colors sm:px-5 ${selected?.id === p.id ? "bg-brand-50/70" : "hover:bg-ink-50/70"}`}>
                    {selected?.id === p.id && <span aria-hidden className="absolute inset-y-0 left-0 w-[3px] bg-brand-600" />}
                    <span className="min-w-0">
                      <span className="block font-medium text-ink-900 whitespace-nowrap">{p.po_number}</span>
                      <span className="block truncate text-ink-500">{p.vendor_name}</span>
                    </span>
                    <span className="shrink-0 font-semibold tabular-nums">{formatMoney(p.total, p.currency)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <div className="min-w-0">{selected && <Review po={selected} onDecided={decided} />}</div>
      </div>

      <Panel title="Decisions" description="Every approval and rejection, newest first." flush className="mt-6">
        {history.loading ? <Loading /> : !history.data?.length ? <EmptyState title="No decisions yet" /> : (
          <div className={table.wrap}>
            <table className={table.table}>
              <thead className={table.thead}>
                <tr>
                  <th className={table.th}>Decision</th>
                  <th className={table.th}>Order</th>
                  <th className={`${table.th} hidden md:table-cell`}>Vendor</th>
                  <th className={`${table.th} text-right`}>Total</th>
                  <th className={`${table.th} hidden sm:table-cell`}>By</th>
                  <th className={`${table.th} hidden lg:table-cell`}>When and why</th>
                </tr>
              </thead>
              <tbody className={table.tbody}>
                {history.data.map((a) => (
                  <tr key={a.id} className={table.row}>
                    <td className={table.td}><Badge tone={a.decision === "APPROVED" ? "success" : "danger"}>{a.decision === "APPROVED" ? "Approved" : "Rejected"}</Badge></td>
                    <td className={table.td}><Link to={`/workspace/${a.request_id}`} className="font-medium text-ink-900 hover:text-brand-600 whitespace-nowrap">{a.po_number}</Link></td>
                    <td className={`${table.td} hidden md:table-cell`}>{a.vendor_name}</td>
                    <td className={`${table.td} text-right tabular-nums`}>{formatMoney(a.total, a.currency)}</td>
                    <td className={`${table.td} hidden sm:table-cell`}>{a.approver_name}</td>
                    <td className={`${table.td} hidden text-ink-500 lg:table-cell`}>
                      {new Date(a.decided_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}{a.reason ? ` · ${a.reason}` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}

function Review({ po, onDecided }: { po: PurchaseOrder; onDecided: () => void }) {
  const [offers, setOffers] = useState<Offers | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  useEffect(() => {
    setOffers(null);
    api.offers(po.request_id).then(setOffers).catch(() => setOffers(null));
  }, [po.request_id]);

  async function decide(approve: boolean) {
    setBusy(true);
    try {
      if (approve) await api.approve(po.id);
      else await api.reject(po.id, reason.trim());
      toast(`${po.po_number} ${approve ? "approved" : "rejected"}`, approve ? "success" : "info");
      setReason("");
      onDecided();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(false);
    }
  }

  const fin = offers?.finance_report;
  const failed = fin?.checks.filter((c) => !c.passed) ?? [];
  return (
    <Card className="overflow-hidden">
      <div className="flex flex-col gap-3 border-b border-line p-5 sm:flex-row sm:items-start sm:justify-between sm:p-6">
        <div className="min-w-0">
          <h2 className="text-xl font-semibold tracking-tight whitespace-nowrap">{po.po_number}</h2>
          <p className="mt-0.5 text-sm text-ink-500">{po.vendor_name} · {po.items.map((i) => `${i.quantity} × ${i.description}`).join(", ")}</p>
        </div>
        <span className="text-[1.75rem] leading-none font-semibold tracking-tight tabular-nums">{formatMoney(po.total, po.currency)}</span>
      </div>

      {!offers ? <Loading label="Loading the recommendation…" /> : (
        <div className="grid gap-6 p-5 sm:p-6 2xl:grid-cols-2">
          <section>
            <h3 className="text-sm font-semibold text-ink-700">AI recommendation</h3>
            {offers.explanation && <p className="mt-2 text-[15px] leading-relaxed text-ink-700">{offers.explanation.summary}</p>}
            {offers.explanation?.risks.length ? (
              <p className="mt-2 flex items-start gap-2 text-sm text-canary-700"><ShieldAlert size={15} className="mt-0.5 shrink-0" /> {offers.explanation.risks.join(" · ")}</p>
            ) : null}
            {offers.ranking.length > 0 && (
              <ol className="mt-4 grid gap-1.5 text-sm">
                {offers.ranking.slice(0, 3).map((c) => (
                  <li key={c.vendor_id} className={`flex items-center justify-between gap-3 rounded-md px-3 py-2 ${c.rank === 1 ? "bg-ok-50" : "bg-ink-50/70"}`}>
                    <span className="flex min-w-0 items-center gap-2"><span className="font-medium">{c.vendor_name}</span>{c.rank === 1 && <Badge tone="success">recommended</Badge>}</span>
                    <span className="shrink-0 text-ink-600 tabular-nums">{formatMoney(c.total, c.currency)} · {c.lead_time_days}d · {c.score}</span>
                  </li>
                ))}
              </ol>
            )}
          </section>

          {fin && (
            <section>
              <h3 className="text-sm font-semibold text-ink-700">Budget validation</h3>
              <dl className="mt-2 grid grid-cols-2 gap-3">
                <div className="rounded-md bg-ink-50/70 p-3"><dt className="text-xs text-ink-500">Budget</dt><dd className="mt-0.5 font-semibold tabular-nums">{formatMoney(offers.requirement?.budget ?? null, po.currency)}</dd></div>
                <div className="rounded-md bg-ink-50/70 p-3"><dt className="text-xs text-ink-500">Left after this</dt><dd className="mt-0.5 font-semibold tabular-nums">{formatMoney(fin.remaining_budget, po.currency)}</dd></div>
              </dl>
              {fin.requires_senior_approval && <p className="mt-3 text-sm text-canary-700">Above the approval threshold — a senior approver should review this.</p>}
              <p className={`mt-3 text-sm ${failed.length ? "text-stamp-700" : "text-ok-700"}`}>
                {failed.length ? `${failed.length} finance check${failed.length > 1 ? "s" : ""} failed` : `All ${fin.checks.length} finance checks passed`}
              </p>
            </section>
          )}
        </div>
      )}

      <div className="flex flex-col gap-2 border-t border-line bg-ink-50/40 p-5 sm:flex-row sm:items-center sm:p-6">
        <Button disabled={busy} onClick={() => decide(true)}><Check size={15} /> Approve</Button>
        <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (needed to reject)" aria-label="Rejection reason" className={`${inputClass} sm:max-w-md sm:flex-1`} />
        <Button variant="danger" disabled={busy || reason.trim().length < 3} onClick={() => decide(false)}><X size={15} /> Reject</Button>
        <Link to={`/workspace/${po.request_id}`} className="text-sm font-medium text-brand-600 hover:text-brand-700 sm:ml-auto">Open full request</Link>
      </div>
    </Card>
  );
}
