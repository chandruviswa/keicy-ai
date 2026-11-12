import { AlertTriangle, Inbox, Loader2, X, type LucideIcon } from "lucide-react";
import { useEffect, type ButtonHTMLAttributes, type ReactNode } from "react";

/* ---------- layout primitives ---------- */

export function PageHeader({ title, description, actions }: { title: ReactNode; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight text-ink-900 sm:text-[1.75rem]">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-[15px] leading-relaxed text-ink-500">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/** Flat panel on a 1px rule. No drop shadow — hierarchy comes from spacing and type. */
export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-[var(--radius-panel)] border border-line bg-white ${className}`}>{children}</section>;
}

/** A Card with a titled header row. `flush` removes body padding (for tables and lists). */
export function Panel({
  title,
  description,
  actions,
  children,
  flush = false,
  className = "",
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  flush?: boolean;
  className?: string;
}) {
  return (
    <Card className={`flex min-w-0 flex-col ${className}`}>
      <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-line px-4 py-3 sm:px-5">
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold text-ink-900">{title}</h2>
          {description && <p className="mt-0.5 text-sm text-ink-500">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </header>
      <div className={flush ? "min-w-0 flex-1" : "min-w-0 flex-1 p-4 sm:p-5"}>{children}</div>
    </Card>
  );
}

/* ---------- figures ---------- */

export function Stat({ label, value, icon: Icon, hint, attention = false }: { label: string; value: ReactNode; icon?: LucideIcon; hint?: ReactNode; attention?: boolean }) {
  return (
    <Card className={`relative overflow-hidden p-4 sm:p-5 ${attention ? "border-canary-500/60" : ""}`}>
      {attention && <span aria-hidden className="absolute inset-y-0 left-0 w-1 bg-canary-500" />}
      <div className="flex items-center gap-2 text-sm text-ink-500">
        {Icon && <Icon size={15} className="text-ink-400" />}
        {label}
      </div>
      <div className="mt-2 text-[clamp(1.25rem,5.5vw,1.75rem)] leading-tight font-semibold tracking-tight [overflow-wrap:anywhere] text-ink-900 tabular-nums sm:text-[2rem] sm:leading-none">{value}</div>
      {hint && <div className="mt-2 text-xs text-ink-400">{hint}</div>}
    </Card>
  );
}

/* ---------- status stamps ---------- */

const TONES = {
  neutral: "border-ink-200 bg-ink-50 text-ink-600",
  info: "border-brand-200 bg-brand-50 text-brand-700",
  success: "border-ok-100 bg-ok-50 text-ok-700",
  warning: "border-canary-100 bg-canary-50 text-canary-700",
  danger: "border-stamp-100 bg-stamp-50 text-stamp-700",
} as const;
export type Tone = keyof typeof TONES;

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={`inline-flex shrink-0 items-center gap-1 rounded-[var(--radius-stamp)] border px-1.5 py-0.5 text-[11.5px] leading-4 font-semibold whitespace-nowrap ${TONES[tone]}`}>
      {children}
    </span>
  );
}

const STATUS: Record<string, [string, Tone]> = {
  // procurement request workflow
  CREATED: ["Submitted", "neutral"],
  ANALYZING: ["Reading request", "info"],
  RESEARCHING: ["Researching", "info"],
  EVALUATING: ["Evaluating", "info"],
  ORDER_DRAFTED: ["Order drafted", "info"],
  FINANCE_VALIDATING: ["Finance check", "info"],
  AWAITING_APPROVAL: ["Awaiting approval", "warning"],
  APPROVED: ["Approved", "success"],
  PAYMENT_PENDING: ["Ready to pay", "warning"],
  PAYMENT_PROCESSING: ["Payment processing", "info"],
  PAYMENT_COMPLETED: ["Paid", "success"],
  RECONCILING: ["Reconciling", "info"],
  COMPLETED: ["Completed", "success"],
  REJECTED: ["Rejected", "danger"],
  FAILED: ["Needs attention", "danger"],
  PAYMENT_FAILED: ["Payment failed", "danger"],
  CANCELLED: ["Cancelled", "neutral"],
  // purchase orders / payments / agent runs
  DRAFT: ["Draft", "neutral"],
  PENDING_APPROVAL: ["Awaiting approval", "warning"],
  PAID: ["Paid", "success"],
  PENDING: ["Awaiting buyer", "info"],
  PROCESSING: ["Processing", "info"],
  RUNNING: ["Running", "info"],
  SUCCEEDED: ["Succeeded", "success"],
  TIMED_OUT: ["Timed out", "danger"],
};

export function statusLabel(status: string): string {
  return STATUS[status]?.[0] ?? status.charAt(0) + status.slice(1).toLowerCase().replaceAll("_", " ");
}

export function statusTone(status: string): Tone {
  return STATUS[status]?.[1] ?? "neutral";
}

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={statusTone(status)} title={status}>{statusLabel(status)}</Badge>;
}

/* ---------- controls ---------- */

type Variant = "primary" | "secondary" | "ghost" | "danger";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-brand-600 text-white hover:bg-brand-700 shadow-[inset_0_-1px_0_rgb(0_0_0/0.15)]",
  secondary: "border border-line bg-white text-ink-700 hover:border-ink-300 hover:bg-ink-50",
  ghost: "text-ink-600 hover:bg-ink-50 hover:text-ink-900",
  danger: "border border-stamp-100 bg-white text-stamp-700 hover:bg-stamp-50",
};

export function Button({ children, variant = "primary", size = "md", className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md" }) {
  const sz = size === "sm" ? "h-8 px-3 text-[13px]" : "h-10 px-4 text-sm";
  return (
    <button
      {...props}
      className={`inline-flex shrink-0 items-center justify-center gap-2 rounded-[var(--radius-control)] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${sz} ${VARIANTS[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

export const inputClass =
  "h-10 w-full rounded-[var(--radius-control)] border border-line bg-white px-3 text-sm text-ink-900 placeholder:text-ink-300 transition-colors hover:border-ink-200 focus:border-brand-500 focus:ring-3 focus:ring-brand-100 focus:outline-none";
export const textareaClass = inputClass.replace("h-10", "min-h-24 py-2.5 leading-relaxed");

export function Field({ label, hint, children, className = "" }: { label: string; hint?: string; children: ReactNode; className?: string }) {
  return (
    <label className={`grid gap-1.5 text-sm ${className}`}>
      <span className="font-medium text-ink-700">{label}</span>
      {children}
      {hint && <span className="text-xs text-ink-400">{hint}</span>}
    </label>
  );
}

/* ---------- tables ---------- */

export const table = {
  wrap: "overflow-x-auto",
  table: "w-full text-sm",
  thead: "border-b border-line bg-ink-50/60 text-left text-xs text-ink-500",
  th: "px-4 py-2.5 font-medium whitespace-nowrap first:pl-4 sm:first:pl-5 last:pr-4 sm:last:pr-5",
  tbody: "divide-y divide-line",
  td: "px-4 py-3 align-middle first:pl-4 sm:first:pl-5 last:pr-4 sm:last:pr-5",
  row: "transition-colors hover:bg-ink-50/70",
};

/* ---------- states ---------- */

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 p-6 text-sm text-ink-500" role="status">
      <Loader2 size={16} className="animate-spin" /> {label}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-start gap-3 rounded-[var(--radius-control)] border border-stamp-100 bg-stamp-50 p-3.5 text-sm text-stamp-700">
      <AlertTriangle size={16} className="mt-0.5 shrink-0" /> <span className="min-w-0 flex-1">{message}</span>
      {onRetry && (
        <button onClick={onRetry} className="font-semibold underline underline-offset-2">
          Try again
        </button>
      )}
    </div>
  );
}

export function EmptyState({ icon: Icon = Inbox, title, description, action }: { icon?: LucideIcon; title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      <span className="grid h-11 w-11 place-items-center rounded-full border border-line bg-ink-50 text-ink-400">
        <Icon size={20} />
      </span>
      <p className="mt-3 font-semibold text-ink-800">{title}</p>
      {description && <p className="mt-1 max-w-sm text-sm text-ink-500">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** Underline tabs; keyboard reachable as buttons with role=tab. */
export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: readonly T[]; value: T; onChange: (t: T) => void }) {
  return (
    <div className="-mx-4 mb-5 overflow-x-auto border-b border-line px-4 sm:mx-0 sm:px-0" role="tablist">
      <div className="flex gap-1">
        {tabs.map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={value === t}
            onClick={() => onChange(t)}
            className={`-mb-px border-b-2 px-3 py-2.5 text-sm font-medium whitespace-nowrap transition-colors ${value === t ? "border-brand-600 text-ink-900" : "border-transparent text-ink-500 hover:text-ink-800"}`}
          >
            {t}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Centered dialog on a dimmed backdrop. Escape and backdrop click close it. */
export function Modal({ title, description, onClose, children, footer }: { title: string; description?: string; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-ink-950/40 sm:items-center sm:p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" aria-label={title}
        className="flex max-h-[92dvh] w-full flex-col rounded-t-[var(--radius-panel)] border border-line bg-white sm:max-w-xl sm:rounded-[var(--radius-panel)]">
        <header className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-ink-900">{title}</h2>
            {description && <p className="mt-0.5 text-sm text-ink-500">{description}</p>}
          </div>
          <button onClick={onClose} aria-label="Close" className="rounded-md p-1.5 text-ink-400 hover:bg-ink-50 hover:text-ink-700"><X size={18} /></button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && <footer className="flex flex-wrap justify-end gap-2 border-t border-line bg-ink-50/40 px-5 py-3">{footer}</footer>}
      </div>
    </div>
  );
}
