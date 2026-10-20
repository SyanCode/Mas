"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { CommandPalette } from "@/components/CommandPalette";
import { Kbd } from "@/components/ui";
import { api, rememberSession, type Session } from "@/lib/api";
import { LanguagePicker, msg, useT } from "@/lib/i18n";

const SessionContext = createContext<Session | null>(null);
export const useSession = () => useContext(SessionContext);

type Theme = "dark" | "light" | "system";
const THEME_KEY = "mas-theme";

function applyTheme(theme: Theme) {
  const dark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

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

const NAV = [
  { href: "/", label: msg("Overview"), match: (p: string) => p === "/" || p.startsWith("/service") },
  { href: "/apps/", label: msg("Apps"), match: (p: string) => p.startsWith("/apps") },
  { href: "/databases/", label: msg("Databases"), match: (p: string) => p.startsWith("/databases") },
  { href: "/health/", label: msg("Health"), match: (p: string) => p.startsWith("/health") },
  { href: "/history/", label: msg("Activity"), match: (p: string) => p.startsWith("/history") },
  { href: "/settings/", label: msg("Settings"), match: (p: string) => p.startsWith("/settings") },
];

// CMS editors (client accounts) only see their sites and their own account.
const EDITOR_NAV = [
  { href: "/cms/", label: msg("My sites"), match: (p: string) => p.startsWith("/cms") },
  { href: "/settings/", label: msg("Account"), match: (p: string) => p.startsWith("/settings") },
];
const EDITOR_PATHS = ["/cms", "/settings"];

function UserMenu({ session }: { session: Session }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState<Theme>("dark");
  useEffect(() => {
    try {
      setTheme((localStorage.getItem(THEME_KEY) as Theme) || "dark");
    } catch {}
  }, []);

  function pick(next: Theme) {
    setTheme(next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {}
    applyTheme(next);
  }

  async function logout() {
    await api("/logout", { method: "POST" }).catch(() => {});
    window.location.href = "/login/";
  }

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex h-8 w-8 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 via-sky-500 to-fuchsia-500 text-xs font-semibold text-white ring-2 ring-white/10"
        title={session.username}
      >
        {session.username.slice(0, 1).toUpperCase()}
      </button>
      {open && (
        <div className="glass absolute right-0 top-10 z-40 w-56 rounded-xl p-1.5 shadow-2xl dark:bg-zinc-950/90" onMouseLeave={() => setOpen(false)}>
          <div className="px-3 py-2">
            <div className="text-sm font-medium">{session.username}</div>
            <div className="text-xs text-zinc-500">{session.totp_enabled ? t("2FA enabled") : t("2FA off")} · v{session.version}</div>
          </div>
          <div className="my-1 h-px bg-zinc-200 dark:bg-white/[0.06]" />
          <div className="flex gap-1 p-1.5">
            {(["dark", "light", "system"] as Theme[]).map((mode) => (
              <button key={mode} onClick={() => pick(mode)} className={`flex-1 rounded-md px-2 py-1 text-xs ${theme === mode ? "bg-zinc-200 dark:bg-white/10" : "text-zinc-500 hover:bg-zinc-100 dark:hover:bg-white/[0.05]"}`}>
                {mode === "dark" ? t("Dark") : mode === "light" ? t("Light") : t("System")}
              </button>
            ))}
          </div>
          <div className="px-3 pb-1.5">
            <LanguagePicker className="w-full" />
          </div>
          <div className="my-1 h-px bg-zinc-200 dark:bg-white/[0.06]" />
          <Link href="/settings/" className="block rounded-md px-3 py-1.5 text-sm hover:bg-zinc-100 dark:hover:bg-white/[0.06]">{session.role === "editor" ? t("Account") : t("Settings")}</Link>
          <button onClick={logout} className="block w-full rounded-md px-3 py-1.5 text-left text-sm text-rose-600 hover:bg-zinc-100 dark:text-rose-400 dark:hover:bg-white/[0.06]">{t("Log out")}</button>
        </div>
      )}
    </div>
  );
}

export function Backdrop() {
  return <div className="mas-backdrop" aria-hidden />;
}

/** Authenticated layout: loads the session, then renders the page. */
export function Shell({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [palette, setPalette] = useState(false);
  const t = useT();
  const pathname = usePathname();
  const router = useRouter();
  const editor = session?.role === "editor";
  const nav = editor ? EDITOR_NAV : NAV;

  useEffect(() => {
    api<Session>("/session").then((s) => {
      rememberSession(s);
      setSession(s);
    }).catch(() => {});
  }, []);

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
        <header className="sticky top-0 z-30 border-b border-zinc-200/70 bg-white/60 backdrop-blur-xl dark:border-white/[0.06] dark:bg-[#050608]/60">
          <div className="mx-auto flex h-14 max-w-7xl items-center gap-6 px-4 sm:px-6">
            <Link href={editor ? "/cms/" : "/"} className="flex items-center gap-2.5">
              <Logo />
              <span className="text-[15px] font-semibold tracking-tight">mas</span>
            </Link>
            <nav className="-mb-px hidden h-14 items-stretch gap-1 md:flex">
              {nav.map((item) => {
                const active = item.match(pathname);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={`relative flex items-center px-3 text-[13px] transition ${active ? "font-medium text-zinc-900 dark:text-white" : "text-zinc-500 hover:text-zinc-900 dark:hover:text-white"}`}
                  >
                    {t(item.label)}
                    {active && <span className="absolute inset-x-2 -bottom-px h-[2px] rounded-full bg-gradient-to-r from-indigo-400 via-sky-400 to-fuchsia-400" />}
                  </Link>
                );
              })}
            </nav>
            <div className="ml-auto flex items-center gap-3">
              {!session.totp_enabled && (
                <Link href="/settings/" className="hidden rounded-full border border-amber-400/30 bg-amber-400/10 px-2.5 py-0.5 text-xs text-amber-600 sm:inline dark:text-amber-300">
                  {t("Enable 2FA")}
                </Link>
              )}
              {!editor && <button
                onClick={() => setPalette(true)}
                className="glass hidden h-8 items-center gap-6 rounded-lg px-3 text-xs text-zinc-500 transition hover:text-zinc-800 sm:flex dark:hover:text-zinc-200"
              >
                {t("Search…")}
                <Kbd>⌘K</Kbd>
              </button>}
              <UserMenu session={session} />
            </div>
          </div>
          <nav className="flex gap-1 overflow-x-auto px-4 pb-2 md:hidden">
            {nav.map((item) => (
              <Link key={item.href} href={item.href} className={`rounded-md px-2.5 py-1 text-xs ${item.match(pathname) ? "bg-zinc-200 dark:bg-white/10" : "text-zinc-500"}`}>
                {t(item.label)}
              </Link>
            ))}
          </nav>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-10 sm:px-6">{children}</main>
      </div>
      {!editor && <CommandPalette open={palette} onClose={() => setPalette(false)} />}
    </SessionContext.Provider>
  );
}
