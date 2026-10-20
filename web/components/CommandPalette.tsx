"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api, type App, type Service } from "@/lib/api";
import { Kbd, StatusDot } from "@/components/ui";

interface Item {
  label: string;
  hint: string;
  href: string;
  group: string;
  status?: string;
}

const PAGES: Item[] = [
  { label: "Overview", hint: "services on this server", href: "/", group: "Pages" },
  { label: "Apps", hint: "deployments from GitHub", href: "/apps/", group: "Pages" },
  { label: "New app", hint: "deploy a repository", href: "/apps/new/", group: "Pages" },
  { label: "Health", hint: "certificates, nginx, DNS", href: "/health/", group: "Pages" },
  { label: "Activity", hint: "audit log", href: "/history/", group: "Pages" },
  { label: "Settings", hint: "domains, GitHub, security", href: "/settings/", group: "Pages" },
];

/** ⌘K / Ctrl+K: jump to any service, app or page. */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<Item[]>(PAGES);
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    setTimeout(() => input.current?.focus(), 0);
    Promise.all([
      api<{ services: Service[] }>("/services").catch(() => ({ services: [] as Service[] })),
      api<App[]>("/apps").catch(() => [] as App[]),
    ]).then(([s, a]) => {
      setItems([
        ...PAGES,
        ...a.map((x) => ({ label: x.id, hint: x.domain ?? x.repo, href: `/apps/view/?id=${encodeURIComponent(x.id)}`, group: "Apps", status: x.last_release?.status })),
        ...s.services.map((x) => ({ label: x.display_name || x.name, hint: x.domains[0] ?? x.manager, href: `/service/?id=${encodeURIComponent(x.id)}`, group: "Services", status: x.status })),
      ]);
    });
  }, [open]);

  const shown = useMemo(() => {
    const q = query.toLowerCase().trim();
    return (q ? items.filter((i) => `${i.label} ${i.hint} ${i.group}`.toLowerCase().includes(q)) : items).slice(0, 40);
  }, [items, query]);

  useEffect(() => setActive(0), [query]);

  if (!open) return null;

  const go = (item?: Item) => {
    if (!item) return;
    onClose();
    window.location.href = item.href;
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-4 pt-[12vh] backdrop-blur-sm" onMouseDown={onClose}>
      <div
        className="glass ring-gradient w-full max-w-xl overflow-hidden rounded-2xl shadow-[0_30px_80px_-20px_rgba(99,102,241,0.45)] dark:bg-zinc-950/80"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-zinc-200/80 px-4 dark:border-white/[0.06]">
          <svg viewBox="0 0 24 24" className="h-4 w-4 text-zinc-500" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
          <input
            ref={input}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, shown.length - 1)); }
              if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
              if (e.key === "Enter") go(shown[active]);
            }}
            placeholder="Search services, apps, pages…"
            className="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-zinc-500"
          />
          <Kbd>esc</Kbd>
        </div>
        <ul className="max-h-[50vh] overflow-y-auto p-2">
          {shown.map((item, i) => (
            <li key={`${item.group}-${item.href}`}>
              {(i === 0 || shown[i - 1].group !== item.group) && (
                <div className="px-2 pb-1 pt-2 text-[11px] font-medium uppercase tracking-wider text-zinc-500">{item.group}</div>
              )}
              <button
                onMouseEnter={() => setActive(i)}
                onClick={() => go(item)}
                className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm ${i === active ? "bg-zinc-100 dark:bg-white/[0.07]" : ""}`}
              >
                {item.status ? <StatusDot status={item.status} size="sm" /> : <span className="h-2 w-2 rounded-full bg-indigo-400/60" />}
                <span className="font-medium">{item.label}</span>
                <span className="truncate text-xs text-zinc-500">{item.hint}</span>
                {i === active && <span className="ml-auto text-xs text-zinc-500">↵</span>}
              </button>
            </li>
          ))}
          {shown.length === 0 && <li className="px-3 py-8 text-center text-sm text-zinc-500">Nothing found.</li>}
        </ul>
      </div>
    </div>
  );
}
