import { ArrowRight, Send, Sparkles } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, formatMoney } from "../api";
import { Button, Card, EmptyState, ErrorState, Field, Loading, PageHeader, Panel, StatusBadge, inputClass, textareaClass } from "../components/ui";
import { useApi } from "../hooks";

const EXAMPLES = [
  "10 laptops, 16GB RAM, under $15,000 by Dec 1",
  "6 ergonomic office chairs, budget $3,000",
  "25 wireless keyboards for the support team, $2,000",
  "4 27-inch 4K monitors, budget $2,000",
];

export default function Workspace() {
  const requests = useApi(api.listRequests);
  const navigate = useNavigate();
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const opt = (k: string) => (String(f.get(k) ?? "").trim() || undefined);
    setSaving(true);
    setFormError(null);
    try {
      const created = await api.createRequest({ raw_text: text, title: opt("title"), budget: opt("budget"), deadline: opt("deadline") });
      await api.execute(created.id);
      navigate(`/workspace/${created.id}`);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : String(err));
      setSaving(false);
    }
  }

  return (
    <>
      <PageHeader title="AI Workspace" description="Describe what you need in your own words. Five agents read it, find real offers, compare vendors and draft a purchase order for approval." />

      <div className="grid gap-4 sm:gap-6 xl:grid-cols-12">
        <Card className="p-4 sm:p-6 xl:col-span-8">
          <form onSubmit={submit} className="grid gap-4">
            <Field label="What do you need?">
              <textarea
                id="raw_text"
                required
                minLength={5}
                rows={4}
                value={text}
                onChange={(e) => setText(e.target.value)}
                className={`${textareaClass} text-[15px]`}
                placeholder="e.g. 10 laptops with 16GB RAM for new engineers, under $15,000, delivered by December 1"
              />
            </Field>
            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="Title" hint="Optional — we'll name it for you">
                <input name="title" className={inputClass} />
              </Field>
              <Field label="Budget (USD)" hint="Optional if it's in the text">
                <input name="budget" type="number" min="0.01" step="0.01" inputMode="decimal" className={inputClass} />
              </Field>
              <Field label="Needed by" hint="Optional">
                <input name="deadline" type="date" className={inputClass} />
              </Field>
            </div>
            {formError && <ErrorState message={formError} />}
            <div className="flex flex-wrap items-center gap-3">
              <Button disabled={saving || text.trim().length < 5}>
                <Send size={15} /> {saving ? "Starting…" : "Submit & run agents"}
              </Button>
              <span className="text-xs text-ink-400">Nothing is bought until an approver signs off.</span>
            </div>
          </form>
        </Card>

        <Card className="p-4 sm:p-6 xl:col-span-4">
          <h2 className="flex items-center gap-2 text-[15px] font-semibold"><Sparkles size={16} className="text-brand-600" /> Try one of these</h2>
          <p className="mt-1 text-sm text-ink-500">Include quantity, any must-have specs and a budget — the agents ask if something is missing.</p>
          <ul className="mt-4 grid gap-2">
            {EXAMPLES.map((ex) => (
              <li key={ex}>
                <button type="button" onClick={() => setText(ex)}
                  className="group flex w-full items-center gap-2 rounded-[var(--radius-control)] border border-line px-3 py-2.5 text-left text-sm text-ink-700 transition-colors hover:border-brand-500 hover:text-ink-900">
                  <span className="flex-1">{ex}</span>
                  <ArrowRight size={14} className="shrink-0 text-ink-300 group-hover:text-brand-600" />
                </button>
              </li>
            ))}
          </ul>
        </Card>

        <Panel title="Your requests" flush className="xl:col-span-12">
          {requests.loading ? (
            <Loading />
          ) : requests.error ? (
            <div className="p-4"><ErrorState message={requests.error} onRetry={requests.reload} /></div>
          ) : !requests.data?.length ? (
            <EmptyState icon={Sparkles} title="No requests yet" description="Describe what you need above — your requests and their progress appear here." />
          ) : (
            <ul className="divide-y divide-line">
              {requests.data.map((r) => (
                <li key={r.id}>
                  <Link to={`/workspace/${r.id}`} className="grid gap-x-6 gap-y-1 px-4 py-3.5 hover:bg-ink-50/70 sm:grid-cols-[minmax(0,1fr)_auto] sm:px-5 lg:grid-cols-[minmax(0,1fr)_10rem_9rem_10rem] lg:items-center">
                    <div className="min-w-0">
                      <p className="truncate font-medium text-ink-900">{r.title ?? "Untitled request"}</p>
                      <p className="truncate text-sm text-ink-500">{r.raw_text}</p>
                    </div>
                    <span className="hidden text-sm text-ink-600 tabular-nums lg:block">{formatMoney(r.budget, r.currency)}</span>
                    <span className="hidden text-sm text-ink-500 lg:block">{r.deadline ? new Date(r.deadline + "T00:00").toLocaleDateString() : "No deadline"}</span>
                    <span className="flex items-center gap-3 sm:justify-end">
                      <StatusBadge status={r.status} />
                      <span className="text-xs text-ink-400 lg:hidden">{new Date(r.created_at).toLocaleDateString()}</span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </>
  );
}
