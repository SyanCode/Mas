"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Kbd } from "@/components/ui";
import { timeAgo, type Repo } from "@/lib/api";
import { useT } from "@/lib/i18n";

type Scope = "all" | "User" | "Organization";

const LANGUAGE_COLORS: Record<string, string> = {
  TypeScript: "#3178c6", JavaScript: "#f1e05a", Python: "#3572a5", PHP: "#4f5d95", HTML: "#e34c26", CSS: "#563d7c",
  Vue: "#41b883", Svelte: "#ff3e00", Go: "#00add8", Rust: "#dea584", Ruby: "#701516", Astro: "#ff5a03",
  Shell: "#89e051", Dockerfile: "#384d54", MDX: "#fcb32c", SCSS: "#c6538c",
};
const PAGE = 25;

function hue(name: string) {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return h % 360;
}

/** Account initial (GitHub avatars are not loaded: the dashboard only fetches from itself). */
function Avatar({ login, org, size = "md" }: { login: string; org: boolean; size?: "sm" | "md" }) {
  const h = hue(login);
  const dim = size === "sm" ? "h-5 w-5 text-[10px]" : "h-7 w-7 text-xs";
  return (
    <span className={`inline-flex shrink-0 items-center justify-center font-semibold text-white ${dim} ${org ? "rounded-md" : "rounded-full"}`}
      style={{ background: `linear-gradient(135deg, hsl(${h} 70% 52%), hsl(${(h + 40) % 360} 70% 40%))` }}>
      {login.slice(0, 1).toUpperCase()}
    </span>
  );
}

function Lock() {
  return (
    <svg viewBox="0 0 16 16" className="h-3 w-3" fill="currentColor" aria-hidden>
      <path d="M4 7V5a4 4 0 1 1 8 0v2h.5A1.5 1.5 0 0 1 14 8.5v5a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 13.5v-5A1.5 1.5 0 0 1 3.5 7H4Zm1.5 0h5V5a2.5 2.5 0 0 0-5 0v2Z" />
    </svg>
  );
}

function Chevron() {
  return <svg viewBox="0 0 16 16" className="h-3.5 w-3.5 opacity-60" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden><path d="m4 6 4 4 4-4" /></svg>;
}

function SearchIcon() {
  return <svg viewBox="0 0 16 16" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden><circle cx="7" cy="7" r="4.5" /><path d="m10.5 10.5 3 3" strokeLinecap="round" /></svg>;
}

interface Props {
  repos: Repo[] | null;
  /** One repository picked (single mode). */
  onSelect?: (repo: Repo) => void;
  /** Several repositories picked (multi mode): their full names. */
  selected?: string[];
  onToggle?: (repo: Repo) => void;
  badge?: (repo: Repo) => ReactNode;
  actionLabel?: string;
  error?: string;
  /** Shown under an empty list. */
  empty?: ReactNode;
}

/** Repository picker in the manner of Vercel's "Import Git Repository". */
export function RepoPicker({ repos, onSelect, selected, onToggle, badge, actionLabel, error, empty }: Props) {
  const t = useT();
  const multi = !!onToggle;
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<Scope>("all");
  const [account, setAccount] = useState<string | null>(null);
  const [accountsOpen, setAccountsOpen] = useState(false);
  const [limit, setLimit] = useState(PAGE);
  const [cursor, setCursor] = useState(0);
  const search = useRef<HTMLInputElement>(null);

  const accounts = useMemo(() => {
    const seen = new Map<string, { login: string; org: boolean; count: number }>();
    for (const r of repos ?? []) {
      const entry = seen.get(r.owner) ?? { login: r.owner, org: r.owner_type === "Organization", count: 0 };
      entry.count += 1;
      seen.set(r.owner, entry);
    }
    return [...seen.values()].sort((a, b) => Number(a.org) - Number(b.org) || a.login.localeCompare(b.login));
  }, [repos]);

  const shown = useMemo(() => {
    const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return (repos ?? [])
      .filter((r) => scope === "all" || r.owner_type === scope)
      .filter((r) => !account || r.owner === account)
      .filter((r) => words.every((w) => `${r.full_name} ${r.description} ${r.language ?? ""}`.toLowerCase().includes(w)))
      .sort((a, b) => Number(a.archived) - Number(b.archived) || (b.pushed_at ?? "").localeCompare(a.pushed_at ?? ""));
  }, [repos, query, scope, account]);

  useEffect(() => {
    setLimit(PAGE);
    setCursor(0);
  }, [query, scope, account]);

  // "/" focuses the search, as on GitHub and Vercel.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) {
        e.preventDefault();
        search.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function pick(r: Repo) {
    if (multi) onToggle?.(r);
    else onSelect?.(r);
  }

  function onSearchKey(e: React.KeyboardEvent) {
    const visible = Math.min(shown.length, limit);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setCursor((c) => Math.min(c + 1, visible - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setCursor((c) => Math.max(c - 1, 0));
    } else if (e.key === "Enter" && shown[cursor]) {
      e.preventDefault();
      pick(shown[cursor]);
    }
  }

  const current = accounts.find((a) => a.login === account);
  const counts = {
    all: repos?.length ?? 0,
    User: (repos ?? []).filter((r) => r.owner_type !== "Organization").length,
    Organization: (repos ?? []).filter((r) => r.owner_type === "Organization").length,
  };
  const scopes: { id: Scope; label: string }[] = [
    { id: "all", label: t("All") }, { id: "User", label: t("Personal") }, { id: "Organization", label: t("Organizations") },
  ];

  return (
    <div className="glass overflow-hidden rounded-2xl">
      <div className="flex flex-col gap-3 border-b border-zinc-200/80 p-3 sm:flex-row sm:items-center dark:border-white/[0.06]">
        <div className="relative shrink-0">
          <button type="button" onClick={() => setAccountsOpen((o) => !o)}
            className="flex h-9 w-full items-center gap-2 rounded-lg border border-zinc-300 bg-white/70 px-2.5 text-sm font-medium sm:w-56 dark:border-white/10 dark:bg-black/30">
            {current ? <Avatar login={current.login} org={current.org} size="sm" /> : <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-zinc-200 text-[10px] dark:bg-white/10">∗</span>}
            <span className="min-w-0 flex-1 truncate text-left">{current ? current.login : t("All accounts")}</span>
            <Chevron />
          </button>
          {accountsOpen && (
            <div className="panel absolute left-0 top-11 z-30 max-h-80 w-64 overflow-y-auto rounded-xl p-1" onMouseLeave={() => setAccountsOpen(false)}>
              <button type="button" onClick={() => { setAccount(null); setAccountsOpen(false); }}
                className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm hover:bg-zinc-100 dark:hover:bg-white/[0.06] ${!account ? "font-medium" : ""}`}>
                {t("All accounts")} <span className="ml-auto text-xs text-zinc-500">{repos?.length ?? 0}</span>
              </button>
              {(["personal", "org"] as const).map((group) => {
                const list = accounts.filter((a) => (group === "org") === a.org && (scope === "all" || (scope === "Organization") === a.org));
                if (!list.length) return null;
                return (
                  <div key={group}>
                    <div className="px-2.5 pb-1 pt-2 text-[10px] font-medium uppercase tracking-wider text-zinc-500">{group === "org" ? t("Organizations") : t("Personal")}</div>
                    {list.map((a) => (
                      <button key={a.login} type="button" onClick={() => { setAccount(a.login); setAccountsOpen(false); }}
                        className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm hover:bg-zinc-100 dark:hover:bg-white/[0.06] ${account === a.login ? "font-medium" : ""}`}>
                        <Avatar login={a.login} org={a.org} size="sm" />
                        <span className="truncate">{a.login}</span>
                        <span className="ml-auto text-xs text-zinc-500">{a.count}</span>
                      </button>
                    ))}
                  </div>
                );
              })}
            </div>
          )}
        </div>
        <div className="relative min-w-0 flex-1">
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500"><SearchIcon /></span>
          <input ref={search} value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={onSearchKey} autoFocus
            placeholder={t("Search repositories…")}
            className="h-9 w-full rounded-lg border border-zinc-300 bg-white/70 pl-9 pr-10 text-sm outline-none transition placeholder:text-zinc-400 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-500/15 dark:border-white/10 dark:bg-black/30" />
          <span className="absolute right-2.5 top-1/2 -translate-y-1/2"><Kbd>/</Kbd></span>
        </div>
      </div>
      <div className="flex items-center gap-1 border-b border-zinc-200/80 px-3 py-2 dark:border-white/[0.06]">
        {scopes.map((s) => (
          <button key={s.id} type="button" onClick={() => { setScope(s.id); if (s.id !== "all" && current && (s.id === "Organization") !== current.org) setAccount(null); }}
            className={`rounded-md px-2.5 py-1 text-xs transition ${scope === s.id ? "bg-zinc-900 text-white dark:bg-white dark:text-zinc-900" : "text-zinc-500 hover:text-zinc-900 dark:hover:text-white"}`}>
            {s.label} <span className="opacity-60">{counts[s.id]}</span>
          </button>
        ))}
        {multi && <span className="ml-auto text-xs text-zinc-500">{t("{n} selected", { n: selected?.length ?? 0 })}</span>}
      </div>
      <ul className="divide-y divide-zinc-200/80 dark:divide-white/[0.06]">
        {shown.slice(0, limit).map((r, i) => {
          const org = r.owner_type === "Organization";
          const checked = !!selected?.includes(r.full_name);
          return (
            <li key={r.full_name}>
              <div onMouseEnter={() => setCursor(i)} onClick={() => pick(r)} role="button" tabIndex={-1}
                className={`group flex cursor-pointer items-center gap-3 px-4 py-3 transition ${cursor === i ? "bg-zinc-100/80 dark:bg-white/[0.04]" : ""} ${r.archived ? "opacity-60" : ""}`}>
                {multi && <input type="checkbox" checked={checked} readOnly className="h-4 w-4 shrink-0 accent-indigo-500" />}
                <Avatar login={r.owner} org={org} />
                <div className="min-w-0 flex-1">
                  <div className="flex min-w-0 items-center gap-2 text-sm">
                    <span className="truncate"><span className="text-zinc-500">{r.owner}/</span><span className="font-semibold">{r.full_name.split("/")[1]}</span></span>
                    {r.private && <span className="text-zinc-500" title={t("private")}><Lock /></span>}
                    {r.archived && <span className="rounded border border-zinc-300 px-1 text-[10px] text-zinc-500 dark:border-white/10">{t("archived")}</span>}
                    {badge?.(r)}
                  </div>
                  <div className="mt-0.5 flex min-w-0 items-center gap-3 text-xs text-zinc-500">
                    {r.language && <span className="flex shrink-0 items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: LANGUAGE_COLORS[r.language] ?? "#8b949e" }} />{r.language}</span>}
                    {r.pushed_at && <span className="shrink-0">{timeAgo(r.pushed_at)}</span>}
                    {r.description && <span className="hidden truncate sm:inline">{r.description}</span>}
                  </div>
                </div>
                {!multi && (
                  <button type="button" onClick={(e) => { e.stopPropagation(); pick(r); }}
                    className="shrink-0 rounded-md bg-zinc-900 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-zinc-700 dark:bg-white dark:text-zinc-900 dark:hover:bg-zinc-200">
                    {actionLabel ?? t("Import")}
                  </button>
                )}
              </div>
            </li>
          );
        })}
        {repos && shown.length === 0 && (
          <li className="px-4 py-10 text-center text-sm text-zinc-500">
            {query || account || scope !== "all" ? t("No repository matches.") : empty ?? t("No repository.")}
          </li>
        )}
        {!repos && !error && Array.from({ length: 5 }).map((_, i) => (
          <li key={i} className="flex items-center gap-3 px-4 py-3">
            <span className="shimmer h-7 w-7 rounded-full bg-zinc-200 dark:bg-white/[0.06]" />
            <span className="shimmer h-3 w-48 rounded bg-zinc-200 dark:bg-white/[0.06]" />
          </li>
        ))}
      </ul>
      {shown.length > limit && (
        <button type="button" onClick={() => setLimit((l) => l + PAGE)}
          className="w-full border-t border-zinc-200/80 py-2.5 text-xs font-medium text-zinc-500 hover:text-zinc-900 dark:border-white/[0.06] dark:hover:text-white">
          {t("Show {n} more", { n: Math.min(PAGE, shown.length - limit) })}
        </button>
      )}
    </div>
  );
}
