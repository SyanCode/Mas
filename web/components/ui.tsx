"use client";

import { forwardRef, useEffect, useRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import type { Status } from "@/lib/api";
import { useT } from "@/lib/i18n";

// -- status -------------------------------------------------------------------

const DOT: Record<string, { color: string; glow: string; ping: boolean }> = {
  running: { color: "bg-emerald-400", glow: "shadow-[0_0_12px_2px_rgba(52,211,153,0.55)]", ping: false },
  live: { color: "bg-emerald-400", glow: "shadow-[0_0_12px_2px_rgba(52,211,153,0.55)]", ping: false },
  building: { color: "bg-amber-400", glow: "shadow-[0_0_12px_2px_rgba(251,191,36,0.55)]", ping: true },
  queued: { color: "bg-amber-300", glow: "shadow-[0_0_10px_1px_rgba(252,211,77,0.4)]", ping: true },
  stopped: { color: "bg-rose-500", glow: "shadow-[0_0_12px_2px_rgba(244,63,94,0.5)]", ping: false },
  failed: { color: "bg-rose-500", glow: "shadow-[0_0_12px_2px_rgba(244,63,94,0.5)]", ping: false },
  errored: { color: "bg-rose-500", glow: "shadow-[0_0_12px_2px_rgba(244,63,94,0.5)]", ping: true },
  superseded: { color: "bg-zinc-500", glow: "", ping: false },
  unknown: { color: "bg-zinc-500", glow: "", ping: false },
};

export function StatusDot({ status, size = "md" }: { status: Status | string; size?: "sm" | "md" | "lg" }) {
  const s = DOT[status] ?? DOT.unknown;
  const dim = { sm: "h-2 w-2", md: "h-2.5 w-2.5", lg: "h-3.5 w-3.5" }[size];
  return (
    <span title={status} className={`relative inline-flex shrink-0 ${dim}`}>
      {s.ping && <span className={`absolute inset-0 animate-ping-slow rounded-full opacity-60 ${s.color}`} />}
      <span className={`relative inline-flex rounded-full ${dim} ${s.color} ${s.glow}`} />
    </span>
  );
}

type Tone = "zinc" | "amber" | "red" | "emerald" | "sky" | "violet";

const TONES: Record<Tone, string> = {
  zinc: "border-zinc-300/70 bg-zinc-100 text-zinc-700 dark:border-white/10 dark:bg-white/[0.04] dark:text-zinc-300",
  amber: "border-amber-300/60 bg-amber-50 text-amber-800 dark:border-amber-400/20 dark:bg-amber-400/10 dark:text-amber-300",
  red: "border-rose-300/60 bg-rose-50 text-rose-700 dark:border-rose-400/20 dark:bg-rose-500/10 dark:text-rose-300",
  emerald: "border-emerald-300/60 bg-emerald-50 text-emerald-700 dark:border-emerald-400/20 dark:bg-emerald-400/10 dark:text-emerald-300",
  sky: "border-sky-300/60 bg-sky-50 text-sky-700 dark:border-sky-400/20 dark:bg-sky-400/10 dark:text-sky-300",
  violet: "border-violet-300/60 bg-violet-50 text-violet-700 dark:border-violet-400/20 dark:bg-violet-400/10 dark:text-violet-300",
};

export function Badge({ children, tone = "zinc", className = "" }: { children: ReactNode; tone?: Tone; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-4 ${TONES[tone]} ${className}`}>
      {children}
    </span>
  );
}

export function ReleaseBadge({ status }: { status: string }) {
  const t = useT();
  const tone = ({ live: "emerald", failed: "red", building: "amber", queued: "amber" } as Record<string, Tone>)[status] ?? "zinc";
  return (
    <Badge tone={tone}>
      <StatusDot status={status} size="sm" />
      {status === "live" ? t("Ready") : status === "building" ? t("Building") : status === "queued" ? t("Queued") : status === "failed" ? t("Error") : t("Superseded")}
    </Badge>
  );
}

// -- controls -------------------------------------------------------------------

type Variant = "primary" | "secondary" | "danger" | "ghost" | "glow";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-zinc-900 text-white hover:bg-zinc-700 dark:bg-white dark:text-zinc-950 dark:hover:bg-zinc-200 shadow-sm",
  secondary: "border border-zinc-300 bg-white/70 hover:bg-white dark:border-white/10 dark:bg-white/[0.04] dark:hover:bg-white/[0.08] backdrop-blur",
  danger: "bg-rose-600 text-white hover:bg-rose-500 shadow-[0_0_20px_-6px_rgba(244,63,94,0.8)]",
  ghost: "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-white/[0.06] dark:hover:text-white",
  glow: "mas-cta text-white bg-[length:200%_100%] hover:bg-right shadow-[0_0_30px_-8px_var(--color-indigo-500)] transition-[background-position]",
};

export function Button({ variant = "secondary", className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      {...props}
      className={`inline-flex h-8 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg px-3 text-[13px] font-medium transition duration-200 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-40 ${VARIANTS[variant]} ${className}`}
    />
  );
}

const FIELD =
  "w-full rounded-lg border border-zinc-300 bg-white/80 px-3 py-2 text-sm text-zinc-900 outline-none transition placeholder:text-zinc-400 " +
  "focus:border-indigo-400 focus:ring-4 focus:ring-indigo-500/15 dark:border-white/10 dark:bg-black/40 dark:text-zinc-100 dark:placeholder:text-zinc-600 dark:focus:border-indigo-400/60";

export const Input = forwardRef<HTMLInputElement, React.ComponentProps<"input">>(function Input(props, ref) {
  return <input ref={ref} {...props} className={`${FIELD} ${props.className ?? ""}`} />;
});

export function Textarea(props: React.ComponentProps<"textarea">) {
  return <textarea {...props} className={`${FIELD} mono text-xs ${props.className ?? ""}`} />;
}

export function Select(props: React.ComponentProps<"select">) {
  return <select {...props} className={`${FIELD} pr-8 ${props.className ?? ""}`} />;
}

export function Label({ title, hint, children }: { title: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block space-y-1.5 text-sm">
      <span className="text-[13px] font-medium text-zinc-700 dark:text-zinc-300">{title}</span>
      {children}
      {hint && <span className="block text-xs text-zinc-500">{hint}</span>}
    </label>
  );
}

// -- layout ------------------------------------------------------------------------

export function Card({ title, actions, children, className = "", padded = true }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; padded?: boolean;
}) {
  return (
    <section className={`glass animate-fade-up overflow-hidden rounded-2xl shadow-[0_1px_0_0_rgba(255,255,255,0.04)_inset,0_20px_50px_-30px_rgba(0,0,0,0.6)] ${className}`}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-zinc-200/80 px-5 py-3 dark:border-white/[0.06]">
          <h2 className="text-[13px] font-semibold tracking-tight">{title}</h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className={padded ? "p-5" : ""}>{children}</div>
    </section>
  );
}

export function PageHeader({ title, subtitle, actions, eyebrow }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode }) {
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4 animate-fade-up">
      <div className="min-w-0">
        {eyebrow && <div className="mb-2 text-xs font-medium uppercase tracking-[0.18em] text-zinc-500">{eyebrow}</div>}
        <h1 className="text-[28px] font-semibold leading-tight tracking-[-0.02em]">{title}</h1>
        {subtitle && <div className="mt-1.5 text-sm text-zinc-500 dark:text-zinc-400">{subtitle}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Stat({ label, value, hint, tone = "zinc" }: { label: string; value: ReactNode; hint?: ReactNode; tone?: Tone }) {
  const accent = { zinc: "from-zinc-400/30", emerald: "from-emerald-400/40", amber: "from-amber-400/40", red: "from-rose-500/40", sky: "from-sky-400/40", violet: "from-violet-400/40" }[tone];
  return (
    <div className="glass relative overflow-hidden rounded-2xl p-4">
      <div className={`pointer-events-none absolute -right-10 -top-10 h-28 w-28 rounded-full bg-gradient-to-br ${accent} to-transparent blur-2xl`} />
      <div className="text-xs font-medium text-zinc-500">{label}</div>
      <div className="mt-1 text-3xl font-semibold tabular-nums tracking-tight">{value}</div>
      {hint && <div className="mt-1 text-xs text-zinc-500">{hint}</div>}
    </div>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="glass flex flex-col items-center rounded-2xl px-6 py-14 text-center">
      <div className="mb-4 h-12 w-12 rounded-2xl bg-gradient-to-br from-indigo-500/30 via-sky-400/20 to-fuchsia-500/30 ring-1 ring-white/10" />
      <h3 className="font-semibold">{title}</h3>
      {children && <p className="mt-1 max-w-md text-sm text-zinc-500">{children}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function Terminal({ children, title, className = "" }: { children: ReactNode; title?: ReactNode; className?: string }) {
  return (
    <div className={`overflow-hidden rounded-xl border border-zinc-800 bg-[#07080b] shadow-[0_0_0_1px_rgba(255,255,255,0.02)] ${className}`}>
      <div className="flex items-center gap-2 border-b border-white/[0.06] px-3 py-2">
        <span className="h-2.5 w-2.5 rounded-full bg-[#ff5f57]" />
        <span className="h-2.5 w-2.5 rounded-full bg-[#febc2e]" />
        <span className="h-2.5 w-2.5 rounded-full bg-[#28c840]" />
        {title && <span className="ml-2 truncate text-xs text-zinc-500">{title}</span>}
      </div>
      {children}
    </div>
  );
}

export function Monogram({ name, size = "md" }: { name: string; size?: "md" | "lg" }) {
  let hash = 0;
  for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  const hue = hash % 360;
  const dim = size === "lg" ? "h-14 w-14 text-xl rounded-2xl" : "h-9 w-9 text-sm rounded-xl";
  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center font-semibold text-white ring-1 ring-white/15 ${dim}`}
      style={{ background: `linear-gradient(135deg, hsl(${hue} 85% 58%), hsl(${(hue + 60) % 360} 80% 45%))` }}
    >
      {name.slice(0, 1).toUpperCase()}
    </span>
  );
}

export function Spinner() {
  return <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent opacity-70" />;
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded border border-zinc-300 bg-zinc-100 px-1.5 py-0.5 font-mono text-[10px] text-zinc-500 dark:border-white/10 dark:bg-white/[0.06]">{children}</kbd>;
}

export function CopyCode({ text }: { text: string }) {
  const t = useT();
  return (
    <div className="group relative">
      <pre className="mono overflow-x-auto whitespace-pre-wrap break-all rounded-lg border border-zinc-200 bg-zinc-50 p-3 pr-16 text-xs dark:border-white/[0.06] dark:bg-black/40">{text}</pre>
      <button
        onClick={() => navigator.clipboard?.writeText(text)}
        className="absolute right-2 top-2 rounded-md border border-zinc-300 bg-white px-2 py-0.5 text-[11px] opacity-70 transition hover:opacity-100 dark:border-white/10 dark:bg-zinc-900"
      >
        {t("Copy")}
      </button>
    </div>
  );
}

// Plain strings (API errors among them) are translated when the dictionary knows them.
export function ErrorText({ children }: { children: ReactNode }) {
  const t = useT();
  if (!children) return null;
  return <p className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-600 dark:text-rose-300">{typeof children === "string" ? t(children) : children}</p>;
}

export function Notice({ children }: { children: ReactNode }) {
  const t = useT();
  if (!children) return null;
  return <p className="rounded-lg border border-emerald-400/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300">{typeof children === "string" ? t(children) : children}</p>;
}

export function ConfirmDialog({
  open, title, message, confirmLabel, danger, onConfirm, onCancel,
}: {
  open: boolean; title: string; message: ReactNode; confirmLabel: string; danger?: boolean;
  onConfirm: () => void; onCancel: () => void;
}) {
  const t = useT();
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onCancel={onCancel}
      className="panel m-auto w-[min(92vw,28rem)] rounded-2xl p-0 text-zinc-900 backdrop:bg-black/60 backdrop:backdrop-blur-sm dark:text-zinc-100"
    >
      <div className="space-y-3 p-6">
        <h3 className="text-base font-semibold">{title}</h3>
        <div className="text-sm text-zinc-600 dark:text-zinc-400">{message}</div>
        <div className="flex justify-end gap-2 pt-3">
          <Button onClick={onCancel}>{t("Cancel")}</Button>
          <Button variant={danger ? "danger" : "primary"} onClick={onConfirm} autoFocus>{confirmLabel}</Button>
        </div>
      </div>
    </dialog>
  );
}
