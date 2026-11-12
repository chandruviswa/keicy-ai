import { ArrowDownRight, ArrowUpRight, BadgeCheck, Clock, PiggyBank, Plus, Wallet } from "lucide-react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, formatMoney, type DashboardSummary } from "../api";
import { useAuth } from "../auth";
import { Button, EmptyState, ErrorState, Loading, PageHeader, Panel, Stat, StatusBadge, statusLabel, table } from "../components/ui";
import { useApi } from "../hooks";

/* Chart colours come from the design tokens: brand blue leads, then ink, canary, stamp green. */
const C = { brand: "#2F5BEA", grid: "#E2E6ED", axis: "#6C7A96", tip: "#F2F4F8" };
const CATEGORY_COLOURS = ["#2F5BEA", "#16233F", "#E9A817", "#1F8A5B", "#8DA6F5", "#9AA6BD"];

const STAGE_TONE: Record<string, string> = {
  intake: "bg-ink-300",
  agents: "bg-brand-500",
  approval: "bg-canary-500",
  payment: "bg-brand-200",
  completed: "bg-ok-600",
  attention: "bg-stamp-600",
  closed: "bg-ink-200",
};

const compact = new Intl.NumberFormat(undefined, { notation: "compact", style: "currency", currency: "USD", maximumFractionDigits: 1 });
const dayLabel = (iso: string) => new Date(iso + "T00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });

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

function duration(minutes: number | null) {
  if (minutes == null) return "—";
  if (minutes < 1) return `${Math.round(minutes * 60)} s`;
  if (minutes < 90) return `${Math.round(minutes)} min`;
  const h = minutes / 60;
  return h < 48 ? `${h.toFixed(1)} h` : `${(h / 24).toFixed(1)} days`;
}

/** Percent change between two 30-day windows; null when there's nothing to compare against. */
function change(now: string, before: string) {
  const a = Number(now), b = Number(before);
  if (!b) return null;
  return ((a - b) / b) * 100;
}

export default function Dashboard() {
  const { user } = useAuth();
  const { data: s, error, loading, reload } = useApi(api.dashboard);

  if (loading) return <Loading />;
  if (error || !s) return <ErrorState message={error ?? "No data"} onRetry={reload} />;
  const m = s.money;
  const delta = change(m.paid_last_30d, m.paid_prev_30d);

  return (
    <>
      <PageHeader
        title={`${greeting()}, ${user?.full_name.split(" ")[0]}`}
        description={
          s.pending_approvals
            ? `${s.pending_approvals} purchase order${s.pending_approvals > 1 ? "s are" : " is"} waiting for approval${s.scope === "mine" ? "" : " across the desk"}.`
            : s.scope === "mine" ? "Here's where your purchasing stands." : "Here's where the whole purchasing desk stands."
        }
        actions={
          <Link to="/workspace">
            <Button><Plus size={16} /> New request</Button>
          </Link>
        }
      />

      {/* ---------- headline figures ---------- */}
      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <Stat label="Paid through PayPal" value={formatMoney(m.paid)} icon={Wallet}
          hint={
            delta == null
              ? `${m.payments} payment${m.payments === 1 ? "" : "s"}${m.payments ? ` · ${formatMoney(m.average_order)} average` : ""}`
              : (
                <span className={`inline-flex items-center gap-0.5 font-medium ${delta >= 0 ? "text-ok-700" : "text-stamp-700"}`}>
                  {delta >= 0 ? <ArrowUpRight size={13} /> : <ArrowDownRight size={13} />}
                  {Math.abs(delta).toFixed(0)}% vs previous 30 days
                </span>
              )
          } />
        <Stat label="Awaiting approval" value={formatMoney(m.awaiting_approval)} icon={BadgeCheck} attention={s.pending_approvals > 0}
          hint={`${s.pending_approvals} order${s.pending_approvals === 1 ? "" : "s"} for a person to review`} />
        <Stat label="Approved, ready to pay" value={formatMoney(m.ready_to_pay)} icon={Clock}
          hint={s.scope === "mine" ? "Your PayPal checkouts to complete" : "Waiting for the requester's PayPal checkout"} />
        <Stat label="Saved vs highest quote" value={formatMoney(m.saved_vs_highest_quote)} icon={PiggyBank}
          hint="By picking better offers on approved orders" />
      </div>

      <div className="mt-4 grid gap-4 sm:mt-6 sm:gap-6 md:grid-cols-2 xl:grid-cols-12">
        {/* ---------- spend trend ---------- */}
        <Panel title="Spend over the last 30 days" description={`${formatMoney(m.paid_last_30d)} captured through PayPal`} className="md:col-span-2 xl:col-span-8">
          {Number(m.paid_last_30d) === 0 ? (
            <EmptyState icon={Wallet} title="No PayPal payments in the last 30 days" description="Captured payments are plotted here by day." />
          ) : (
            <div className="h-60 sm:h-72">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={s.spend_by_day.map((d) => ({ ...d, amount: Number(d.amount) }))} margin={{ left: 4, right: 8, top: 8 }}>
                  <CartesianGrid vertical={false} stroke={C.grid} />
                  <XAxis dataKey="date" tickFormatter={dayLabel} tick={{ fontSize: 12, fill: C.axis }} axisLine={false} tickLine={false} minTickGap={28} />
                  <YAxis tickFormatter={(v) => compact.format(v)} tick={{ fontSize: 12, fill: C.axis }} axisLine={false} tickLine={false} width={56} />
                  <Tooltip cursor={{ fill: C.tip }} labelFormatter={(l) => dayLabel(String(l))} formatter={(v) => [formatMoney(String(v)), "Paid"]}
                    contentStyle={{ borderRadius: 8, border: `1px solid ${C.grid}`, fontSize: 13 }} />
                  <Bar dataKey="amount" fill={C.brand} radius={[3, 3, 0, 0]} maxBarSize={22} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </Panel>

        {/* ---------- pipeline ---------- */}
        <Panel title="Request pipeline" description={`${s.total_requests} request${s.total_requests === 1 ? "" : "s"} · ${s.active_workflows} with the agents now`} className="md:col-span-2 xl:col-span-4">
          <Pipeline stages={s.pipeline} total={s.total_requests} />
        </Panel>

        {/* ---------- where the money went ---------- */}
        <Panel title="Top vendors" description="Paid through PayPal" className="xl:col-span-4">
          <VendorBars rows={s.spend_by_vendor} />
        </Panel>
        <Panel title="Spend by category" description="Paid order lines" className="xl:col-span-4">
          <CategoryDonut rows={s.spend_by_category} />
        </Panel>
        <Panel title="How the desk is performing" className="md:col-span-2 xl:col-span-4">
          <Performance s={s} />
        </Panel>

        {/* ---------- recent work ---------- */}
        <Panel title="Recent purchase orders" flush className="md:col-span-2 xl:col-span-7"
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
                        <Link to={`/workspace/${p.request_id}`} className="font-medium whitespace-nowrap text-ink-900 hover:text-brand-600">{p.po_number}</Link>
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

        <Panel title="Recent activity" flush className="md:col-span-2 xl:col-span-5">
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

        {/* ---------- catalogue health ---------- */}
        <Panel title="Catalogue health" description="What the agents can buy from right now" className="md:col-span-2 xl:col-span-12"
          actions={<Link to="/vendors" className="text-sm font-medium text-brand-600 hover:text-brand-700">Open Vendor Intelligence</Link>}>
          <Catalogue c={s.catalogue} />
        </Panel>
      </div>
    </>
  );
}

function Pipeline({ stages, total }: { stages: DashboardSummary["pipeline"]; total: number }) {
  if (!total) return <EmptyState title="No requests yet" description="Requests appear here as they move from intake to payment." />;
  const shown = stages.filter((st) => st.count > 0 || ["agents", "approval", "payment", "completed"].includes(st.key));
  return (
    <div>
      {/* One bar, split by stage, so the shape of the desk reads at a glance. */}
      <div className="flex h-2.5 overflow-hidden rounded-full bg-ink-50" aria-hidden>
        {stages.filter((st) => st.count).map((st) => (
          <span key={st.key} className={STAGE_TONE[st.key]} style={{ width: `${(st.count / total) * 100}%` }} />
        ))}
      </div>
      <ul className="mt-4 grid gap-2.5 text-sm">
        {shown.map((st) => (
          <li key={st.key} className="flex items-center gap-3">
            <span className={`h-2.5 w-2.5 shrink-0 rounded-sm ${STAGE_TONE[st.key]}`} />
            <span className="flex-1 text-ink-700">{st.label}</span>
            <span className="font-semibold tabular-nums text-ink-900">{st.count}</span>
            <span className="w-10 text-right text-xs text-ink-400 tabular-nums">{Math.round((st.count / total) * 100)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function VendorBars({ rows }: { rows: DashboardSummary["spend_by_vendor"] }) {
  if (!rows.length) return <EmptyState title="No vendor spend yet" description="Vendors you've paid through PayPal are ranked here." />;
  const top = Math.max(...rows.map((r) => Number(r.amount)));
  return (
    <ul className="grid gap-3.5">
      {rows.map((r) => (
        <li key={r.vendor}>
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span className="min-w-0 truncate font-medium text-ink-900">{r.vendor}</span>
            <span className="shrink-0 font-semibold tabular-nums">{formatMoney(r.amount)}</span>
          </div>
          <div className="mt-1.5 flex items-center gap-2">
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-50">
              <div className="h-full rounded-full bg-brand-500" style={{ width: `${(Number(r.amount) / top) * 100}%` }} />
            </div>
            <span className="w-16 shrink-0 text-right text-xs text-ink-400">{r.orders} order{r.orders === 1 ? "" : "s"}</span>
          </div>
        </li>
      ))}
    </ul>
  );
}

function CategoryDonut({ rows }: { rows: DashboardSummary["spend_by_category"] }) {
  if (!rows.length) return <EmptyState title="No category spend yet" description="Paid order lines are grouped by product category." />;
  const data = rows.map((r) => ({ name: r.category, value: Number(r.amount) }));
  const total = data.reduce((a, d) => a + d.value, 0);
  return (
    <div className="flex flex-col items-center gap-4 sm:flex-row xl:flex-col 2xl:flex-row">
      <div className="relative h-40 w-40 shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={data} dataKey="value" nameKey="name" innerRadius="62%" outerRadius="100%" paddingAngle={data.length > 1 ? 2 : 0} stroke="none">
              {data.map((_, i) => <Cell key={i} fill={CATEGORY_COLOURS[i % CATEGORY_COLOURS.length]} />)}
            </Pie>
            <Tooltip formatter={(v) => formatMoney(String(v))} contentStyle={{ borderRadius: 8, border: `1px solid ${C.grid}`, fontSize: 13 }} />
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 grid place-items-center text-center">
          <div>
            <div className="text-xs text-ink-500">Total</div>
            <div className="text-sm font-semibold tabular-nums">{compact.format(total)}</div>
          </div>
        </div>
      </div>
      <ul className="grid w-full flex-1 gap-2 text-sm">
        {data.map((d, i) => (
          <li key={d.name} className="flex items-center gap-2.5">
            <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: CATEGORY_COLOURS[i % CATEGORY_COLOURS.length] }} />
            <span className="flex-1 capitalize text-ink-700">{d.name}</span>
            <span className="font-medium tabular-nums">{formatMoney(String(d.value))}</span>
            <span className="w-10 text-right text-xs text-ink-400 tabular-nums">{Math.round((d.value / total) * 100)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Performance({ s }: { s: DashboardSummary }) {
  const rows: [string, string, string][] = [
    ["Request to purchase order", duration(s.speed.median_minutes_to_po), "median time for the agents to draft and check an order"],
    ["Waiting for a decision", s.speed.median_hours_to_decision == null ? "—" : duration(s.speed.median_hours_to_decision * 60), "median time from approval request to decision"],
    ["Request to paid & reconciled", s.speed.median_hours_to_complete == null ? "—" : duration(s.speed.median_hours_to_complete * 60), "median end-to-end time"],
    ["Approval rate", s.approvals.approval_rate == null ? "—" : `${s.approvals.approval_rate}%`, `${s.approvals.approved} approved · ${s.approvals.rejected} rejected`],
    ["Agent runs succeeded", s.agents.success_rate == null ? "—" : `${s.agents.success_rate}%`, `${s.agents.runs} runs${s.agents.median_seconds != null ? ` · ${duration(s.agents.median_seconds / 60)} median` : ""}`],
  ];
  return (
    <dl className="grid gap-3.5">
      {rows.map(([label, value, hint]) => (
        <div key={label} className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <dt className="text-sm font-medium text-ink-800">{label}</dt>
            <dd className="text-xs text-ink-400">{hint}</dd>
          </div>
          <dd className="shrink-0 text-base font-semibold tabular-nums text-ink-900">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Catalogue({ c }: { c: DashboardSummary["catalogue"] }) {
  if (!c.vendors && !c.products) {
    return (
      <EmptyState title="Your catalogue is empty" description="Add vendors, products and their offers — or import the starter catalogue — so the agents have something to buy."
        action={<Link to="/vendors"><Button variant="secondary">Open Vendor Intelligence</Button></Link>} />
    );
  }
  const items: [string, number, string][] = [
    ["Active vendors", c.vendors, ""],
    ["Products", c.products, ""],
    ["Live offers", c.live_offers, "in stock, in date, not paused"],
    ["Expiring in 14 days", c.expiring_soon, c.expiring_soon ? "text-canary-700" : ""],
    ["Out of stock", c.out_of_stock, c.out_of_stock ? "text-canary-700" : ""],
    ["Expired", c.expired, c.expired ? "text-stamp-700" : ""],
  ];
  return (
    <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-control)] border border-line bg-line sm:grid-cols-3 xl:grid-cols-6">
      {items.map(([label, n, tone]) => (
        <div key={label} className="bg-white px-4 py-3">
          <dt className="text-xs text-ink-500">{label}</dt>
          <dd className={`mt-1 text-xl font-semibold tabular-nums ${tone.startsWith("text-") ? tone : "text-ink-900"}`}>{n}</dd>
          {!tone.startsWith("text-") && tone && <dd className="text-[11px] text-ink-400">{tone}</dd>}
        </div>
      ))}
    </dl>
  );
}
