import { Activity, BadgeCheck, ClipboardList, Plus, Wallet } from "lucide-react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, formatMoney } from "../api";
import { useAuth } from "../auth";
import { Button, EmptyState, ErrorState, Loading, PageHeader, Panel, Stat, StatusBadge, statusLabel, table } from "../components/ui";
import { useApi } from "../hooks";

function activityText(e: { event_type: string; payload: Record<string, unknown> }) {
  const p = e.payload;
  if (e.event_type === "workflow.state_changed") return `moved to ${statusLabel(String(p.to)).toLowerCase()}`;
  if (e.event_type.startsWith("agent.")) return `${p.agent} agent ${e.event_type.split(".")[1]}`;
  if (e.event_type === "approval.required") return `needs approval · ${p.po_number}`;
  if (e.event_type === "payment.completed") return "payment captured";
  if (e.event_type === "payment.order_created") return "PayPal checkout started";
  return e.event_type.replace(".", " ").replaceAll("_", " ");
}

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export default function Dashboard() {
  const { user } = useAuth();
  const { data, error, loading, reload } = useApi(() =>
    Promise.all([api.dashboard(), api.products()]).then(([summary, products]) => ({ summary, products })),
  );

  if (loading) return <Loading />;
  if (error || !data) return <ErrorState message={error ?? "No data"} onRetry={reload} />;
  const s = data.summary;
  const byCategory = Object.entries(
    data.products.reduce<Record<string, number>>((acc, p) => ((acc[p.category] = (acc[p.category] ?? 0) + p.offers.length), acc), {}),
  ).map(([category, count]) => ({ category, count }));

  return (
    <>
      <PageHeader
        title={`${greeting()}, ${user?.full_name.split(" ")[0]}`}
        description={s.pending_approvals ? `${s.pending_approvals} purchase order${s.pending_approvals > 1 ? "s are" : " is"} waiting for approval.` : "Here's where your purchasing stands."}
        actions={
          <Link to="/workspace">
            <Button><Plus size={16} /> New request</Button>
          </Link>
        }
      />

      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <Stat label="Requests" value={s.total_requests} icon={ClipboardList} />
        <Stat label="Agents working" value={s.active_workflows} icon={Activity} hint="requests being processed now" />
        <Stat label="Awaiting approval" value={s.pending_approvals} icon={BadgeCheck} attention={s.pending_approvals > 0} />
        <Stat label="Paid through PayPal" value={formatMoney(s.total_transaction_value)} icon={Wallet}
          hint={`${s.completed_purchases} completed purchase${s.completed_purchases === 1 ? "" : "s"}`} />
      </div>

      <div className="mt-4 grid gap-4 sm:mt-6 sm:gap-6 xl:grid-cols-12">
        <Panel title="Recent purchase orders" flush className="xl:col-span-7"
          actions={<Link to="/purchase-orders" className="text-sm font-medium text-brand-600 hover:text-brand-700">View all</Link>}>
          {s.recent_purchase_orders.length === 0 ? (
            <EmptyState title="No purchase orders yet" description="Submit a request and the agents will draft one." />
          ) : (
            <div className={table.wrap}>
              <table className={table.table}>
                <thead className={table.thead}>
                  <tr><th className={table.th}>Order</th><th className={`${table.th} hidden sm:table-cell`}>Vendor</th><th className={`${table.th} text-right`}>Total</th><th className={table.th}>Status</th></tr>
                </thead>
                <tbody className={table.tbody}>
                  {s.recent_purchase_orders.map((p) => (
                    <tr key={p.id} className={table.row}>
                      <td className={table.td}>
                        <Link to={`/workspace/${p.request_id}`} className="font-medium text-ink-900 hover:text-brand-600 whitespace-nowrap">{p.po_number}</Link>
                        <div className="text-xs text-ink-500 sm:hidden">{p.vendor_name}</div>
                      </td>
                      <td className={`${table.td} hidden text-ink-600 sm:table-cell`}>{p.vendor_name}</td>
                      <td className={`${table.td} text-right font-medium tabular-nums`}>{formatMoney(p.total, p.currency)}</td>
                      <td className={table.td}><StatusBadge status={p.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <Panel title="Recent activity" flush className="xl:col-span-5">
          {s.recent_activity.length === 0 ? (
            <EmptyState title="Nothing has happened yet" description="Activity from agents, approvers and payments shows up here." />
          ) : (
            <ol className="divide-y divide-line">
              {s.recent_activity.slice(0, 8).map((a) => (
                <li key={a.seq}>
                  <Link to={`/workspace/${a.request_id}`} className="flex items-baseline gap-3 px-4 py-2.5 text-sm hover:bg-ink-50/70 sm:px-5">
                    <span className="min-w-0 flex-1 truncate">
                      <span className="font-medium text-ink-900">{a.request_title ?? "Untitled request"}</span>{" "}
                      <span className="text-ink-500">{activityText(a)}</span>
                    </span>
                    <time className="shrink-0 text-xs text-ink-400">{new Date(a.created_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</time>
                  </Link>
                </li>
              ))}
            </ol>
          )}
        </Panel>

        <Panel title="Offers in the catalogue" description="Competing vendor offers the agents can choose from, by category." className="xl:col-span-12">
          <div className="h-56 sm:h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={byCategory} margin={{ left: -16, right: 8, top: 8 }}>
                <CartesianGrid vertical={false} stroke="#E2E6ED" />
                <XAxis dataKey="category" tick={{ fontSize: 12, fill: "#4B5875" }} axisLine={false} tickLine={false} />
                <YAxis allowDecimals={false} tick={{ fontSize: 12, fill: "#6C7A96" }} axisLine={false} tickLine={false} />
                <Tooltip cursor={{ fill: "#F2F4F8" }} contentStyle={{ borderRadius: 8, border: "1px solid #E2E6ED", fontSize: 13 }} />
                <Bar dataKey="count" name="Offers" fill="#2F5BEA" radius={[4, 4, 0, 0]} maxBarSize={72} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      </div>
    </>
  );
}
