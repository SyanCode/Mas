"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { Activity, Database, Gauge, HeartPulse, LayoutDashboard, Menu, Newspaper, Rocket, Search, Server, Settings, type LucideIcon } from "lucide-react";
import { CommandPalette } from "@/components/CommandPalette";
import { ModePicker, ThemePicker } from "@/components/ThemePicker";
import { Kbd } from "@/components/ui";
import { api, rememberSession, type Session } from "@/lib/api";
import { LanguagePicker, msg, useT } from "@/lib/i18n";

const SessionContext = createContext<Session | null>(null);
export const useSession = () => useContext(SessionContext);

export function Logo({ className = "h-6 w-6" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden>
      <defs>
        <linearGradient id="mas-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#818cf8" />
          <stop offset="0.5" stopColor="#38bdf8" />
          <stop offset="1" stopColor="#e879f9" />
        </linearGradient>
      </defs>
      <path d="M16 2 29 9.5v13L16 30 3 22.5v-13Z" fill="none" stroke="url(#mas-g)" strokeWidth="2.2" strokeLinejoin="round" />
      <path d="M9.5 21V12l6.5 6 6.5-6v9" fill="none" stroke="url(#mas-g)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

interface NavEntry { href: string; label: string; icon: LucideIcon; match: (p: string) => boolean }

const NAV_GROUPS: { title: string; items: NavEntry[] }[] = [
  { title: "", items: [
    { href: "/", label: msg("Overview"), icon: LayoutDashboard, match: (p) => p === "/" || p.startsWith("/service") },
    { href: "/apps/", label: msg("Apps"), icon: Rocket, match: (p) => p.startsWith("/apps") || p.startsWith("/projects") },
    { href: "/databases/", label: msg("Databases"), icon: Database, match: (p) => p.startsWith("/databases") },
  ] },
  { title: msg("Server"), items: [
    { href: "/resources/", label: msg("Resources"), icon: Gauge, match: (p) => p.startsWith("/resources") },
    { href: "/cluster/", label: msg("Servers"), icon: Server, match: (p) => p.startsWith("/cluster") },
    { href: "/health/", label: msg("Health"), icon: HeartPulse, match: (p) => p.startsWith("/health") },
    { href: "/history/", label: msg("Activity"), icon: Activity, match: (p) => p.startsWith("/history") },
  ] },
  { title: msg("Content"), items: [
    { href: "/cms/", label: msg("CMS"), icon: Newspaper, match: (p) => p.startsWith("/cms") },
  ] },
  { title: msg("Account"), items: [
    { href: "/settings/", label: msg("Settings"), icon: Settings, match: (p) => p.startsWith("/settings") },
  ] },
];

// CMS editors (client accounts) only see their sites and their own account.
const EDITOR_NAV: NavEntry[] = [
  { href: "/cms/", label: msg("My sites"), icon: Newspaper, match: (p) => p.startsWith("/cms") },
  { href: "/settings/", label: msg("Account"), icon: Settings, match: (p) => p.startsWith("/settings") },
];
const EDITOR_PATHS = ["/cms", "/settings"];

function UserMenu({ session }: { session: Session }) {
  const t = useT();
  const [open, setOpen] = useState(false);

  async function logout() {
    await api("/logout", { method: "POST" }).catch(() => {});
    window.location.href = "/login/";
  }

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="mas-cta flex h-8 w-8 items-center justify-center rounded-full text-xs font-semibold text-white ring-2 ring-white/10"
        title={session.username}
      >
        {session.username.slice(0, 1).toUpperCase()}
      </button>
      {open && (
        <div className="panel absolute right-0 top-10 z-40 w-72 rounded-xl p-1.5" onMouseLeave={() => setOpen(false)}>
          <div className="px-3 py-2">
            <div className="text-sm font-medium">{session.username}</div>
            <div className="text-xs text-zinc-500">{session.totp_enabled ? t("2FA enabled") : t("2FA off")} · v{session.version}</div>
          </div>
          <div className="my-1 h-px bg-zinc-200 dark:bg-white/[0.06]" />
          <div className="space-y-2 p-1.5">
            <div className="px-1.5 text-[11px] font-medium uppercase tracking-wider text-zinc-500">{t("Theme")}</div>
            <ThemePicker />
            <ModePicker />
          </div>
          <div className="px-3 pb-1.5">
            <LanguagePicker className="w-full" />
          </div>
          <div className="my-1 h-px bg-zinc-200 dark:bg-white/[0.06]" />
          <Link href="/settings/" className="block rounded-md px-3 py-1.5 text-sm hover:bg-zinc-100 dark:hover:bg-white/[0.06]">{session.role !== "admin" ? t("Account") : t("Settings")}</Link>
          <button onClick={logout} className="block w-full rounded-md px-3 py-1.5 text-left text-sm text-rose-600 hover:bg-zinc-100 dark:text-rose-400 dark:hover:bg-white/[0.06]">{t("Log out")}</button>
        </div>
      )}
    </div>
  );
}

export function Backdrop() {
  return <div className="mas-backdrop" aria-hidden />;
}

/** Authenticated layout: a sidebar shared by the whole dashboard (the CMS has the same), then the page. */
export function Shell({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [palette, setPalette] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const t = useT();
  const pathname = usePathname();
  const router = useRouter();
  const editor = !!session && session.role !== "admin";

  useEffect(() => {
    api<Session>("/session").then((s) => {
      rememberSession(s);
      setSession(s);
    }).catch(() => {});
  }, []);

  useEffect(() => setDrawer(false), [pathname]);

  useEffect(() => {
    if (editor && !EDITOR_PATHS.some((p) => pathname.startsWith(p))) router.replace("/cms/");
  }, [editor, pathname, router]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (editor) return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((p) => !p);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editor]);

  if (!session || (editor && !EDITOR_PATHS.some((p) => pathname.startsWith(p)))) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Backdrop />
        <Logo className="h-10 w-10 animate-pulse" />
      </div>
    );
  }

  return (
    <SessionContext.Provider value={session}>
      <Backdrop />
      <div className="min-h-screen">
        <aside className="fixed inset-y-0 left-0 z-40 hidden w-60 lg:block"><SideNav editor={editor} pathname={pathname} version={session.version} /></aside>
        {drawer && (
          <div className="fixed inset-0 z-50 lg:hidden">
            <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={() => setDrawer(false)} />
            <aside className="absolute inset-y-0 left-0 w-64"><SideNav editor={editor} pathname={pathname} version={session.version} /></aside>
          </div>
        )}
        <div className="lg:pl-60">
          <header className="mas-header sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-zinc-200/70 px-5 backdrop-blur-xl sm:px-8 dark:border-white/[0.06]">
            <button type="button" onClick={() => setDrawer(true)} aria-label={t("Menu")} className="flex h-9 w-9 items-center justify-center rounded-lg text-zinc-600 hover:bg-zinc-900/[0.05] lg:hidden dark:text-zinc-300 dark:hover:bg-white/[0.07]">
              <Menu className="size-5" />
            </button>
            <Link href={editor ? "/cms/" : "/"} className="flex items-center gap-2 lg:hidden"><Logo /><span className="text-[15px] font-semibold tracking-tight">mas</span></Link>
            <div className="ml-auto flex shrink-0 items-center gap-3">
              {!session.totp_enabled && (
                <Link href="/settings/" className="hidden whitespace-nowrap rounded-full border border-amber-400/30 bg-amber-400/10 px-2.5 py-0.5 text-xs text-amber-600 sm:inline dark:text-amber-300">
                  {t("Enable 2FA")}
                </Link>
              )}
              {!editor && <button
                onClick={() => setPalette(true)}
                className="glass hidden h-9 items-center gap-8 whitespace-nowrap rounded-lg px-3 text-xs text-zinc-500 transition hover:text-zinc-800 sm:flex dark:hover:text-zinc-200"
                title={t("Search…")}
              >
                <span className="flex items-center gap-2"><Search className="size-3.5" />{t("Search…")}</span>
                <Kbd>⌘K</Kbd>
              </button>}
              <UserMenu session={session} />
            </div>
          </header>
          <main className="mx-auto max-w-6xl px-5 py-10 sm:px-10">{children}</main>
        </div>
      </div>
      {!editor && <CommandPalette open={palette} onClose={() => setPalette(false)} />}
    </SessionContext.Provider>
  );
}

function SideLink({ href, active, icon: Icon, children }: { href: string; active: boolean; icon: LucideIcon; children: ReactNode }) {
  return (
    <Link href={href}
      className={`group flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] transition ${active
        ? "bg-zinc-900/[0.05] font-medium text-zinc-900 dark:bg-white/[0.08] dark:text-white"
        : "text-zinc-500 hover:bg-zinc-900/[0.04] hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-white/[0.05] dark:hover:text-white"}`}>
      <Icon className={`size-4 shrink-0 transition ${active ? "text-indigo-500" : "opacity-70 group-hover:opacity-100"}`} />
      <span className="min-w-0 flex-1 truncate">{children}</span>
    </Link>
  );
}

function SideNav({ editor, pathname, version }: { editor: boolean; pathname: string; version: string }) {
  const t = useT();
  const groups = editor ? [{ title: "", items: EDITOR_NAV }] : NAV_GROUPS;
  return (
    <div className="flex h-full flex-col border-r border-zinc-200/70 bg-[var(--bg)] dark:border-white/[0.06]">
      <Link href={editor ? "/cms/" : "/"} className="flex h-16 shrink-0 items-center gap-2.5 px-5">
        <Logo className="h-7 w-7" />
        <span className="text-[17px] font-semibold tracking-tight">mas</span>
      </Link>
      <nav className="flex-1 space-y-1 overflow-y-auto px-3 pb-4">
        {groups.map((g, i) => (
          <div key={i} className="space-y-0.5">
            {g.title && <div className="px-3 pb-1.5 pt-6 text-[10.5px] font-medium uppercase tracking-[0.12em] text-zinc-400">{t(g.title)}</div>}
            {g.items.map((item) => <SideLink key={item.href} href={item.href} icon={item.icon} active={item.match(pathname)}>{t(item.label)}</SideLink>)}
          </div>
        ))}
      </nav>
      <div className="border-t border-zinc-200/70 px-5 py-3 text-[11px] text-zinc-400 dark:border-white/[0.06]">Mas v{version}</div>
    </div>
  );
}
