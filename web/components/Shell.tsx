"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, rememberSession, type Session } from "@/lib/api";

const SessionContext = createContext<Session | null>(null);
export const useSession = () => useContext(SessionContext);

type Theme = "system" | "light" | "dark";
const THEME_KEY = "mas-theme";

function applyTheme(theme: Theme) {
  const dark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("system");
  useEffect(() => {
    try {
      setTheme((localStorage.getItem(THEME_KEY) as Theme) || "system");
    } catch {}
  }, []);
  const next: Record<Theme, Theme> = { system: "dark", dark: "light", light: "system" };
  const icon: Record<Theme, string> = { system: "◐", dark: "☾", light: "☀" };
  return (
    <button
      title={`Theme: ${theme}`}
      className="rounded-md px-2 py-1 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800"
      onClick={() => {
        const t = next[theme];
        setTheme(t);
        try {
          localStorage.setItem(THEME_KEY, t);
        } catch {}
        applyTheme(t);
      }}
    >
      {icon[theme]}
    </button>
  );
}

const NAV = [
  { href: "/", label: "Services" },
  { href: "/apps/", label: "Apps" },
  { href: "/health/", label: "Health" },
  { href: "/history/", label: "History" },
  { href: "/settings/", label: "Settings" },
];

/** Authenticated layout: loads the session, then renders the page. */
export function Shell({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const pathname = usePathname();

  useEffect(() => {
    api<Session>("/session").then((s) => {
      rememberSession(s);
      setSession(s);
    }).catch(() => {});
  }, []);

  async function logout() {
    await api("/logout", { method: "POST" }).catch(() => {});
    window.location.href = "/login/";
  }

  if (!session) {
    return <div className="flex min-h-screen items-center justify-center text-sm text-zinc-500">Loading…</div>;
  }

  return (
    <SessionContext.Provider value={session}>
      <div className="min-h-screen">
        <header className="sticky top-0 z-10 border-b border-zinc-200 bg-white/80 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/80">
          <div className="mx-auto flex h-12 max-w-6xl items-center gap-4 px-4">
            <Link href="/" className="font-semibold tracking-tight">mas</Link>
            <nav className="flex gap-1 text-sm">
              {NAV.map((item) => {
                const active = item.href === "/" ? pathname === "/" || pathname.startsWith("/service") : pathname.startsWith(item.href);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={`rounded-md px-2.5 py-1 ${active ? "bg-zinc-100 font-medium dark:bg-zinc-800" : "text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"}`}
                  >
                    {item.label}
                  </Link>
                );
              })}
            </nav>
            <div className="ml-auto flex items-center gap-2 text-sm">
              {!session.totp_enabled && (
                <Link href="/settings/" className="hidden rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800 sm:inline dark:bg-amber-500/15 dark:text-amber-300">
                  2FA off
                </Link>
              )}
              <span className="hidden text-zinc-500 sm:inline">{session.username}</span>
              <ThemeToggle />
              <button onClick={logout} className="rounded-md px-2 py-1 text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800">
                Log out
              </button>
            </div>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </div>
    </SessionContext.Provider>
  );
}
