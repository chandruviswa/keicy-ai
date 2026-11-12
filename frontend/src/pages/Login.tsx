import { ArrowRight, Loader2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { api, type DemoAccounts } from "../api";
import { useAuth } from "../auth";
import { Button, ErrorState, Field, inputClass } from "../components/ui";

const ROLE_HINT: Record<string, string> = {
  requester: "Asks for things and pays for approved orders",
  approver: "Reviews recommendations and signs off",
  admin: "Everything, plus audit and model usage",
};

/* The routing slip, in miniature: how a request travels. */
const ROUTE = ["Request", "Agents research & compare", "Purchase order", "Human approval", "PayPal payment", "Reconciled receipt"];

export default function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [demo, setDemo] = useState<DemoAccounts | null>(null);

  useEffect(() => {
    api.demoAccounts().then(setDemo).catch(() => setDemo(null));
  }, []);

  async function signIn(e?: FormEvent, as?: { email: string; password: string }) {
    e?.preventDefault();
    setBusy(as?.email ?? "form");
    setError(null);
    try {
      await login(as?.email ?? email, as?.password ?? password);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(null);
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
      {/* Brand panel */}
      <aside className="relative hidden overflow-hidden bg-ink-900 px-10 py-12 text-ink-100 lg:flex lg:flex-col xl:px-16">
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-lg bg-white text-lg font-extrabold text-ink-900">P</span>
          <span className="text-lg font-semibold text-white">Procura</span>
        </div>
        <div className="my-auto max-w-xl py-12">
          <h1 className="text-4xl leading-[1.1] font-semibold tracking-tight text-white xl:text-5xl">From a sentence to a paid purchase order.</h1>
          <p className="mt-5 max-w-md text-lg leading-relaxed text-ink-300">
            Describe what your team needs. Agents find real offers, draft the order and check the budget — a person approves, PayPal pays, and every step is on record.
          </p>
          <ol className="mt-10 grid gap-0">
            {ROUTE.map((step, i) => (
              <li key={step} className="relative flex items-center gap-4 pb-5 last:pb-0">
                {i < ROUTE.length - 1 && <span aria-hidden className="absolute top-7 bottom-0 left-[13px] w-px bg-white/15" />}
                <span className={`grid h-7 w-7 shrink-0 place-items-center rounded-full border text-xs font-semibold ${i === 3 ? "border-canary-500 bg-canary-500 text-ink-950" : "border-white/25 text-ink-200"}`}>
                  {i + 1}
                </span>
                <span className={i === 3 ? "font-semibold text-white" : "text-ink-200"}>{step}</span>
              </li>
            ))}
          </ol>
        </div>
        <p className="text-sm text-ink-400">Money only moves after a human approves.</p>
      </aside>

      {/* Sign-in */}
      <main className="flex items-center justify-center px-4 py-10 sm:px-8">
        <div className="w-full max-w-[420px]">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <span className="grid h-10 w-10 place-items-center rounded-lg bg-ink-900 text-lg font-extrabold text-white">P</span>
            <div>
              <p className="font-semibold">Procura</p>
              <p className="text-sm text-ink-500">From a sentence to a paid purchase order</p>
            </div>
          </div>

          <h2 className="text-2xl font-semibold tracking-tight">Sign in</h2>
          <p className="mt-1 text-sm text-ink-500">Use your work email and password.</p>

          <form onSubmit={signIn} className="mt-6 grid gap-4">
            <Field label="Email">
              <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} className={inputClass} />
            </Field>
            <Field label="Password">
              <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} className={inputClass} />
            </Field>
            {error && <ErrorState message={error} />}
            <Button disabled={!!busy} className="mt-1 w-full">
              {busy === "form" ? <Loader2 size={16} className="animate-spin" /> : null} Sign in
            </Button>
          </form>

          {demo?.enabled && demo.password && (
            <div className="mt-10">
              <div className="mb-3 flex items-center gap-3 text-sm text-ink-500">
                <span className="h-px flex-1 bg-line" />
                Or sign in with a team account
                <span className="h-px flex-1 bg-line" />
              </div>
              <ul className="grid gap-2">
                {demo.accounts.filter((a) => a.email !== "requester2@procura.local").map((a) => (
                  <li key={a.email}>
                    <button
                      disabled={!!busy}
                      onClick={() => signIn(undefined, { email: a.email, password: demo.password! })}
                      className="group flex w-full items-center gap-3 rounded-[var(--radius-control)] border border-line bg-white px-3.5 py-3 text-left transition-colors hover:border-brand-500 disabled:opacity-50"
                    >
                      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-ink-50 text-xs font-semibold text-ink-700">
                        {a.name.split(" ").map((p) => p[0]).join("")}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-semibold text-ink-900">{a.name} <span className="font-normal text-ink-400">· {a.role}</span></span>
                        <span className="block truncate text-xs text-ink-500">{ROLE_HINT[a.role]}</span>
                      </span>
                      {busy === a.email ? <Loader2 size={16} className="animate-spin text-ink-400" /> : <ArrowRight size={16} className="text-ink-300 transition-colors group-hover:text-brand-600" />}
                    </button>
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-xs text-ink-400">Live accounts — every request runs the real AI agents and real PayPal checkout.</p>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
