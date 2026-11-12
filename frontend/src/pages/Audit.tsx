import { useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type Intelligence as Intel, type RunLogs } from "../api";
import { Badge, EmptyState, ErrorState, Loading, PageHeader, Panel, Stat, StatusBadge, Tabs, table } from "../components/ui";
import { useApi } from "../hooks";

const TABS = ["Agents", "Models", "Tools", "Audit trail"] as const;
type Tab = (typeof TABS)[number];
const pct = (v: number | null) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const secs = (ms: number | null) => (ms == null ? "—" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);

export default function Audit() {
  const [tab, setTab] = useState<Tab>("Agents");
  const intel = useApi(api.intelligence);
  const totals = intel.data && {
    runs: intel.data.agents.reduce((s, a) => s + a.runs, 0),
    modelCalls: intel.data.models.reduce((s, m) => s + m.calls, 0),
    toolCalls: intel.data.tools.reduce((s, t) => s + t.calls, 0),
    blocked: intel.data.tools.reduce((s, t) => s + t.blocked, 0),
  };

  return (
    <>
      <PageHeader title="Intelligence & Audit" description="How the agents, models and tools behaved — and a permanent record of every action taken in Keicy." />
      {totals && (
        <div className="mb-6 grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
          <Stat label="Agent runs" value={totals.runs} />
          <Stat label="Model calls" value={totals.modelCalls} />
          <Stat label="Tool calls" value={totals.toolCalls} />
          <Stat label="Blocked tool calls" value={totals.blocked} hint="an agent tried something it isn't allowed to do" />
        </div>
      )}
      <Tabs tabs={TABS} value={tab} onChange={setTab} />
      {intel.error && tab !== "Audit trail" && <ErrorState message={intel.error} onRetry={intel.reload} />}
      {tab === "Agents" && (intel.loading ? <Loading /> : intel.data && <Agents intel={intel.data} />)}
      {tab === "Models" && (intel.loading ? <Loading /> : intel.data && <Models intel={intel.data} />)}
      {tab === "Tools" && (intel.loading ? <Loading /> : intel.data && <Tools intel={intel.data} />)}
      {tab === "Audit trail" && <Trail />}
    </>
  );
}

function Agents({ intel }: { intel: Intel }) {
  const [agent, setAgent] = useState("");
  const runs = useApi(() => api.agentRuns({ agent: agent || undefined }), [agent]);
  const [logs, setLogs] = useState<RunLogs | null>(null);
  return (
    <div className="grid gap-4 sm:gap-6 2xl:grid-cols-12">
      <Panel title="Time per agent" description="Average duration of a run, in seconds." className="2xl:col-span-7">
        <div className="h-56 sm:h-64">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={intel.agents.map((a) => ({ ...a, seconds: +(a.avg_duration_ms / 1000).toFixed(1) }))} margin={{ left: -16, right: 8, top: 8 }}>
              <CartesianGrid vertical={false} stroke="#E2E6ED" />
              <XAxis dataKey="agent" tick={{ fontSize: 12, fill: "#4B5875" }} axisLine={false} tickLine={false} interval={0} />
              <YAxis tick={{ fontSize: 12, fill: "#6C7A96" }} axisLine={false} tickLine={false} />
              <Tooltip cursor={{ fill: "#F2F4F8" }} contentStyle={{ borderRadius: 8, border: "1px solid #E2E6ED", fontSize: 13 }} />
              <Bar dataKey="seconds" name="seconds" fill="#2F5BEA" radius={[4, 4, 0, 0]} maxBarSize={56} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Panel>
      <Panel title="Reliability" flush className="2xl:col-span-5">
        <SimpleTable head={["Agent", "Runs", "Success", "Timeouts", "Average"]} align={[0, 1, 1, 1, 1]}
          rows={intel.agents.map((a) => [a.agent, a.runs, pct(a.success_rate), a.timeouts, secs(a.avg_duration_ms)])} />
      </Panel>

      <Panel title="Recent agent runs" flush className="2xl:col-span-12"
        actions={
          <select value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Agent"
            className="h-9 rounded-[var(--radius-control)] border border-line bg-white px-2.5 text-sm">
            <option value="">All agents</option>
            {intel.agents.map((a) => <option key={a.agent}>{a.agent}</option>)}
          </select>
        }>
        {runs.loading ? <Loading /> : !runs.data?.length ? <EmptyState title="No runs yet" /> : (
          <div className={table.wrap}>
            <table className={table.table}>
              <thead className={table.thead}>
                <tr>
                  <th className={table.th}>Agent</th>
                  <th className={`${table.th} hidden md:table-cell`}>Request</th>
                  <th className={table.th}>Result</th>
                  <th className={`${table.th} text-right`}>Duration</th>
                  <th className={`${table.th} hidden text-right sm:table-cell`}>Tools</th>
                  <th className={`${table.th} text-right`}><span className="sr-only">Trace</span></th>
                </tr>
              </thead>
              <tbody className={table.tbody}>
                {runs.data.map((r) => (
                  <tr key={r.id} className={table.row}>
                    <td className={`${table.td} font-medium`}>{r.agent_name}</td>
                    <td className={`${table.td} hidden md:table-cell`}><Link className="text-ink-700 hover:text-brand-600" to={`/workspace/${r.request_id}`}>{r.request_title ?? "Untitled request"}</Link></td>
                    <td className={table.td}>
                      <StatusBadge status={r.status} />
                      {r.error && <div className="mt-1 max-w-[22rem] truncate text-xs text-stamp-600" title={r.error}>{r.error}</div>}
                    </td>
                    <td className={`${table.td} text-right tabular-nums`}>{secs(r.duration_ms)}</td>
                    <td className={`${table.td} hidden text-right tabular-nums sm:table-cell`}>{r.tool_calls}</td>
                    <td className={`${table.td} text-right`}><button className="text-sm font-medium text-brand-600 hover:text-brand-700" onClick={() => api.runLogs(r.id).then(setLogs)}>Trace</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {logs && (
        <Panel title={`Trace · ${logs.run.agent_name} agent`} className="2xl:col-span-12"
          actions={<button className="text-sm text-ink-500 hover:text-ink-900" onClick={() => setLogs(null)}>Close</button>}>
          <h3 className="text-sm font-semibold text-ink-700">Model calls</h3>
          <SimpleTable head={["Provider", "Model", "Purpose", "Latency", "Tokens in / out", "Result"]} align={[0, 0, 0, 1, 1, 0]}
            rows={logs.model_calls.map((m) => [m.provider, m.model, m.purpose ?? "", `${m.latency_ms} ms`, `${m.input_tokens ?? "—"} / ${m.output_tokens ?? "—"}`, m.success ? (m.fallback_used ? "ok (fallback)" : "ok") : m.error_type ?? "failed"])}
            empty="No model calls — this agent works by rules only." />
          <h3 className="mt-6 text-sm font-semibold text-ink-700">Tool calls</h3>
          <SimpleTable head={["Tool", "Writes data", "Result", "Duration", "Error"]} align={[0, 0, 0, 1, 0]}
            rows={logs.tool_calls.map((t) => [t.tool_name, t.is_write ? "yes" : "", t.status.toLowerCase(), `${t.duration_ms ?? 0} ms`, t.error ?? ""])} />
        </Panel>
      )}
    </div>
  );
}

function Models({ intel }: { intel: Intel }) {
  return (
    <div className="grid gap-4 sm:gap-6">
      <Panel title="Model usage" description="Every call through the AI gateway, including retries and fallbacks." flush>
        <SimpleTable head={["Provider", "Model", "Calls", "Success", "Fallbacks", "Average", "p95", "Tokens in / out"]} align={[0, 0, 1, 1, 1, 1, 1, 1]}
          rows={intel.models.map((m) => [m.provider, m.model, m.calls, pct(m.success_rate), m.fallback_calls, `${m.avg_latency_ms} ms`, `${m.p95_latency_ms} ms`, `${m.input_tokens.toLocaleString()} / ${m.output_tokens.toLocaleString()}`])}
          empty="No model calls yet" />
      </Panel>
      {intel.model_errors.length > 0 && (
        <Panel title="Errors by type" flush>
          <SimpleTable head={["Provider", "Error", "Count"]} align={[0, 0, 1]} rows={intel.model_errors.map((e) => [e.provider, e.error_type, e.count])} />
        </Panel>
      )}
    </div>
  );
}

function Tools({ intel }: { intel: Intel }) {
  return (
    <Panel title="Tool usage" description="Agents only reach data through these tools; anything outside an agent's permissions is blocked and logged." flush>
      <SimpleTable head={["Tool", "Calls", "Success", "Blocked", "Repeats prevented", "Average"]} align={[0, 1, 1, 1, 1, 1]}
        rows={intel.tools.map((t) => [t.tool, t.calls, pct(t.success_rate), t.blocked, t.duplicates, `${t.avg_ms} ms`])} empty="No tool calls yet" />
    </Panel>
  );
}

function Trail() {
  const [action, setAction] = useState("");
  const logs = useApi(() => api.auditLogs({ action: action || undefined }), [action]);
  const filters = [["", "Everything"], ["auth.", "Sign-ins"], ["procurement_request.", "Requests"], ["workflow.", "Workflow"], ["purchase_order.", "Approvals"], ["payment.", "Payments"]];
  return (
    <Panel title="Audit trail" description="Permanent: the database refuses to change or delete these entries." flush
      actions={
        <select value={action} onChange={(e) => setAction(e.target.value)} aria-label="Action"
          className="h-9 rounded-[var(--radius-control)] border border-line bg-white px-2.5 text-sm">
          {filters.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      }>
      {logs.loading ? <Loading /> : logs.error ? <div className="p-4"><ErrorState message={logs.error} /></div> : !logs.data?.length ? <EmptyState title="No entries" /> : (
        <div className={table.wrap}>
          <table className={table.table}>
            <thead className={table.thead}>
              <tr>
                <th className={table.th}>When</th>
                <th className={table.th}>Who</th>
                <th className={table.th}>Action</th>
                <th className={`${table.th} hidden lg:table-cell`}>Details</th>
              </tr>
            </thead>
            <tbody className={table.tbody}>
              {logs.data.map((a) => (
                <tr key={a.id} className="align-top">
                  <td className={`${table.td} whitespace-nowrap text-ink-500`}>{new Date(a.created_at).toLocaleString([], { dateStyle: "short", timeStyle: "medium" })}</td>
                  <td className={table.td}>{a.actor}<div className="text-xs text-ink-400">{a.actor_type}</div></td>
                  <td className={table.td}><Badge tone={a.action.includes("failed") || a.action.includes("discrepancy") ? "danger" : "neutral"}>{a.action}</Badge><div className="mt-1 text-xs text-ink-400">{a.entity_type.replaceAll("_", " ")}</div></td>
                  <td className={`${table.td} hidden max-w-xl text-xs break-all text-ink-500 lg:table-cell`}>{JSON.stringify(a.details).slice(0, 240)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function SimpleTable({ head, rows, align, empty = "No data yet" }: { head: string[]; rows: (string | number)[][]; align: number[]; empty?: string }) {
  if (!rows.length) return <p className="px-4 py-5 text-sm text-ink-500 sm:px-5">{empty}</p>;
  return (
    <div className={table.wrap}>
      <table className={table.table}>
        <thead className={table.thead}><tr>{head.map((h, i) => <th key={h} className={`${table.th} ${align[i] ? "text-right" : ""}`}>{h}</th>)}</tr></thead>
        <tbody className={table.tbody}>{rows.map((r, i) => <tr key={i} className={table.row}>{r.map((c, j) => <td key={j} className={`${table.td} ${align[j] ? "text-right tabular-nums" : ""} ${j === 0 ? "font-medium text-ink-900" : ""}`}>{c}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}
