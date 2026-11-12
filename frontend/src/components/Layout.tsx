import {
  BadgeCheck,
  Building2,
  ChevronRight,
  CreditCard,
  FileText,
  LayoutDashboard,
  LogOut,
  Menu,
  ScrollText,
  Sparkles,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { api, type Health } from "../api";
import { useAuth } from "../auth";

type Role = "requester" | "approver" | "admin";
type NavItem = { to: string; label: string; icon: LucideIcon; roles?: Role[] };

const GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "Buy",
    items: [
      { to: "/", label: "Overview", icon: LayoutDashboard },
      { to: "/workspace", label: "AI Workspace", icon: Sparkles },
      { to: "/vendors", label: "Vendor Intelligence", icon: Building2 },
    ],
  },
  {
    label: "Approve & pay",
    items: [
      { to: "/purchase-orders", label: "Purchase Orders", icon: FileText },
      { to: "/approvals", label: "Approval Center", icon: BadgeCheck, roles: ["approver", "admin"] },
      { to: "/payments", label: "Payment Center", icon: CreditCard },
    ],
  },
  {
    label: "Oversight",
    items: [{ to: "/audit", label: "Intelligence & Audit", icon: ScrollText, roles: ["approver", "admin"] }],
  },
];
export const NAV = GROUPS.flatMap((g) => g.items);

function initials(name: string) {
  return name.split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();
}

function usePendingApprovals(enabled: boolean) {
  const [count, setCount] = useState(0);
  const { pathname } = useLocation();
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const load = () => api.purchaseOrders("PENDING_APPROVAL").then((p) => alive && setCount(p.length)).catch(() => {});
    load();
    const t = setInterval(load, 30_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [enabled, pathname]);
  return count;
}

/** Full sidebar (desktop + mobile drawer) or icon rail (tablet). */
function Sidebar({ compact = false, onNavigate }: { compact?: boolean; onNavigate?: () => void }) {
  const { user, logout } = useAuth();
  const pending = usePendingApprovals(!!user && user.role !== "requester");
  const visible = (n: NavItem) => !n.roles || (user && n.roles.includes(user.role));

  return (
    <nav aria-label="Main" className="flex h-full flex-col bg-ink-900 text-ink-100">
      <Link to="/" onClick={onNavigate} className={`flex items-center gap-3 px-5 pt-5 pb-6 ${compact ? "justify-center px-0" : ""}`}>
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-white text-[15px] font-extrabold text-ink-900">P</span>
        {!compact && (
          <span className="leading-tight">
            <span className="block text-[15px] font-semibold text-white">Procura</span>
            <span className="block text-xs text-ink-300">Procurement desk</span>
          </span>
        )}
      </Link>

      <div className="flex-1 overflow-y-auto px-3">
        {GROUPS.map((g) => {
          const items = g.items.filter(visible);
          if (!items.length) return null;
          return (
            <div key={g.label} className="mb-5">
              {!compact && <p className="mb-1.5 px-3 text-xs font-medium text-ink-400">{g.label}</p>}
              <ul className="grid gap-0.5">
                {items.map(({ to, label, icon: Icon }) => (
                  <li key={to}>
                    <NavLink
                      to={to}
                      end={to === "/"}
                      onClick={onNavigate}
                      title={compact ? label : undefined}
                      aria-label={compact ? label : undefined}
                      className={({ isActive }) =>
                        `group relative flex items-center gap-3 rounded-lg py-2 text-sm font-medium transition-colors ${compact ? "justify-center px-0" : "px-3"} ${
                          isActive ? "bg-white/10 text-white" : "text-ink-300 hover:bg-white/5 hover:text-white"
                        }`
                      }
                    >
                      {({ isActive }) => (
                        <>
                          {isActive && <span aria-hidden className="absolute top-1.5 bottom-1.5 left-0 w-[3px] rounded-r bg-brand-500" />}
                          <Icon size={18} className="shrink-0" />
                          {!compact && <span className="flex-1">{label}</span>}
                          {to === "/approvals" && pending > 0 && (
                            <span className={`rounded-full bg-canary-500 px-1.5 text-[11px] leading-[18px] font-bold text-ink-950 ${compact ? "absolute -top-0.5 right-2" : ""}`}>
                              {pending}
                            </span>
                          )}
                        </>
                      )}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>

      {user && (
        <div className={`border-t border-white/10 p-3 ${compact ? "flex flex-col items-center gap-2" : ""}`}>
          {compact ? (
            <>
              <span title={`${user.full_name} · ${user.role}`} className="grid h-9 w-9 place-items-center rounded-full bg-white/10 text-xs font-semibold text-white">
                {initials(user.full_name)}
              </span>
              <button onClick={logout} aria-label="Sign out" title="Sign out" className="rounded-lg p-2 text-ink-300 hover:bg-white/5 hover:text-white">
                <LogOut size={17} />
              </button>
            </>
          ) : (
            <div className="flex items-center gap-3 rounded-lg px-2 py-1.5">
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-white/10 text-xs font-semibold text-white">{initials(user.full_name)}</span>
              <span className="min-w-0 flex-1 leading-tight">
                <span className="block truncate text-sm font-medium text-white">{user.full_name}</span>
                <span className="block truncate text-xs text-ink-300 capitalize">{user.role}</span>
              </span>
              <button onClick={logout} className="inline-flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium text-ink-300 hover:bg-white/5 hover:text-white">
                <LogOut size={14} /> Sign out
              </button>
            </div>
          )}
        </div>
      )}
    </nav>
  );
}

/* ---------- top bar ---------- */

function SystemStatus() {
  const [health, setHealth] = useState<Health | null>(null);
  const [down, setDown] = useState(false);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const load = () => api.health().then((h) => { setHealth(h); setDown(false); }).catch(() => setDown(true));
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const llmOk = !!health && (health.llm.cloud_configured || health.llm.ollama_reachable);
  const services = [
    { name: "Database", ok: !down && !!health?.database, detail: health?.schema_version ? `schema ${health.schema_version}` : "" },
    { name: "AI models", ok: !down && llmOk, detail: health ? (health.llm.cloud_configured ? `cloud: ${health.llm.cloud_model}` : `local: ${health.llm.ollama_model}`) : "" },
    { name: "PayPal", ok: !down && !!health?.paypal.configured, detail: health ? health.paypal.mode : "" },
  ];
  const bad = services.filter((s) => !s.ok).length;
  const label = down ? "Server unreachable" : !health ? "Checking…" : bad ? `${bad} service${bad > 1 ? "s" : ""} need attention` : "All systems normal";

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="inline-flex h-9 items-center gap-2 rounded-full border border-line bg-white px-3 text-xs font-medium text-ink-600 hover:border-ink-200"
      >
        <span className={`h-2 w-2 rounded-full ${!health && !down ? "bg-ink-300" : bad || down ? "bg-canary-500" : "bg-ok-600"}`} />
        <span className="hidden sm:inline">{label}</span>
        <span className="sm:hidden">Status</span>
      </button>
      {open && (
        <div className="absolute right-0 z-30 mt-2 w-64 rounded-[var(--radius-panel)] border border-line bg-white p-2 shadow-lg shadow-ink-900/10">
          {services.map((s) => (
            <div key={s.name} className="flex items-center gap-2.5 rounded-md px-2 py-2 text-sm">
              <span className={`h-2 w-2 rounded-full ${s.ok ? "bg-ok-600" : "bg-canary-500"}`} />
              <span className="flex-1 font-medium text-ink-800">{s.name}</span>
              <span className="truncate text-xs text-ink-400">{s.ok ? s.detail : "unavailable"}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function TopBar({ onMenu }: { onMenu: () => void }) {
  const { pathname } = useLocation();
  const current = NAV.find((n) => (n.to === "/" ? pathname === "/" : pathname.startsWith(n.to)));
  const isRequest = /^\/workspace\/.+/.test(pathname);

  return (
    <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-line bg-white/90 px-4 backdrop-blur sm:px-6 xl:px-8">
      <button onClick={onMenu} className="-ml-1 rounded-lg p-2 text-ink-600 hover:bg-ink-50 md:hidden" aria-label="Open menu">
        <Menu size={20} />
      </button>
      <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1.5 text-sm">
        {isRequest ? (
          <>
            <Link to="/workspace" className="truncate text-ink-500 hover:text-ink-900">AI Workspace</Link>
            <ChevronRight size={14} className="shrink-0 text-ink-300" />
            <span className="truncate font-medium text-ink-900">Request</span>
          </>
        ) : (
          <span className="truncate font-medium text-ink-900">{current?.label}</span>
        )}
      </nav>
      <div className="ml-auto flex items-center gap-2">
        <SystemStatus />
      </div>
    </header>
  );
}

export function Layout() {
  const [open, setOpen] = useState(false);
  const { pathname } = useLocation();
  useEffect(() => {
    setOpen(false);
  }, [pathname]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="min-h-screen md:pl-[76px] lg:pl-64">
      {/* desktop: full sidebar · tablet: icon rail */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 lg:block">
        <Sidebar />
      </aside>
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[76px] md:block lg:hidden">
        <Sidebar compact />
      </aside>

      {/* phone: drawer */}
      {open && (
        <div className="fixed inset-0 z-40 md:hidden" role="dialog" aria-modal="true" aria-label="Menu">
          <div className="absolute inset-0 bg-ink-950/40" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-[min(18rem,85vw)] shadow-2xl">
            <button onClick={() => setOpen(false)} className="absolute top-4 right-3 z-10 rounded-md p-1.5 text-ink-300 hover:bg-white/10" aria-label="Close menu">
              <X size={18} />
            </button>
            <Sidebar onNavigate={() => setOpen(false)} />
          </aside>
        </div>
      )}

      <TopBar onMenu={() => setOpen(true)} />
      <main className="px-4 py-6 sm:px-6 sm:py-8 xl:px-8 2xl:px-10">
        <Outlet />
      </main>
    </div>
  );
}
