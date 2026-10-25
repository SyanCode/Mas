"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
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

const NAV = [
  { href: "/", label: msg("Overview"), match: (p: string) => p === "/" || p.startsWith("/service") },
  { href: "/apps/", label: msg("Apps"), match: (p: string) => p.startsWith("/apps") || p.startsWith("/projects") },
  { href: "/databases/", label: msg("Databases"), match: (p: string) => p.startsWith("/databases") },
  { href: "/resources/", label: msg("Resources"), match: (p: string) => p.startsWith("/resources") },
  { href: "/cluster/", label: msg("Servers"), match: (p: string) => p.startsWith("/cluster") },
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
          <Link href="/settings/" className="block rounded-md px-3 py-1.5 text-sm hover:bg-zinc-100 dark:hover:bg-white/[0.06]">{session.role === "editor" ? t("Account") : t("Settings")}</Link>
          <button onClick={logout} className="block w-full rounded-md px-3 py-1.5 text-left text-sm text-rose-600 hover:bg-zinc-100 dark:text-rose-400 dark:hover:bg-white/[0.06]">{t("Log out")}</button>
        </div>
      )}
    </div>
  );
}

type NavItem = (typeof NAV)[number];

/** The tabs that fit in the header, the others in "More": measured, so it holds with every theme and font. */
function PriorityNav({ items, pathname }: { items: NavItem[]; pathname: string }) {
  const t = useT();
  const box = useRef<HTMLDivElement>(null);
  const ruler = useRef<HTMLDivElement>(null);
  const [shown, setShown] = useState(items.length);
  const [open, setOpen] = useState(false);

  useLayoutEffect(() => {
    const fit = () => {
      if (!box.current || !ruler.current) return;
      const widths = [...ruler.current.children].map((c) => (c as HTMLElement).offsetWidth);
      const moreWidth = widths.pop() ?? 0;
      const room = box.current.clientWidth;
      let used = widths.reduce((a, b) => a + b, 0);
      if (used <= room) return setShown(widths.length);
      let n = widths.length;
      while (n > 0 && used + moreWidth > room) used -= widths[--n];
      setShown(n);
    };
    fit();
    const observer = new ResizeObserver(fit);
    if (box.current) observer.observe(box.current);
    if (ruler.current) observer.observe(ruler.current);  // a theme with another font
    document.fonts?.ready.then(fit).catch(() => {});
    return () => observer.disconnect();
  }, [items, t]);

  useEffect(() => setOpen(false), [pathname]);

  const tab = (active: boolean) => `relative flex shrink-0 items-center whitespace-nowrap px-3 text-[13px] transition ${active ? "font-medium text-zinc-900 dark:text-white" : "text-zinc-500 hover:text-zinc-900 dark:hover:text-white"}`;
  const line = <span className="mas-accent-line absolute inset-x-2 -bottom-px h-[2px] rounded-full bg-gradient-to-r from-indigo-400 via-sky-400 to-fuchsia-400" />;
  const hidden = items.slice(shown);
  const hiddenActive = hidden.find((i) => i.match(pathname));
  return (
    <div ref={box} className="relative hidden h-14 min-w-0 flex-1 md:block">
      <div ref={ruler} aria-hidden className="pointer-events-none invisible absolute left-0 top-0 flex h-0 overflow-hidden">
        {items.map((item) => <span key={item.href} className={tab(true)}>{t(item.label)}</span>)}
        <span className={tab(true)}>{t("More")} ▾</span>
      </div>
      <nav className="-mb-px flex h-14 items-stretch">
        {items.slice(0, shown).map((item) => {
          const active = item.match(pathname);
          return <Link key={item.href} href={item.href} className={tab(active)}>{t(item.label)}{active && line}</Link>;
        })}
        {hidden.length > 0 && (
          <div className="relative flex" onMouseLeave={() => setOpen(false)}>
            <button type="button" onClick={() => setOpen((o) => !o)} className={tab(!!hiddenActive)} aria-expanded={open}>
              {hiddenActive ? t(hiddenActive.label) : t("More")} ▾{hiddenActive && line}
            </button>
            {open && (
              <div className="panel absolute left-0 top-12 z-40 min-w-44 rounded-xl p-1">
                {hidden.map((item) => (
                  <Link key={item.href} href={item.href}
                    className={`block whitespace-nowrap rounded-md px-3 py-1.5 text-sm hover:bg-zinc-100 dark:hover:bg-white/[0.06] ${item.match(pathname) ? "font-medium" : ""}`}>
                    {t(item.label)}
                  </Link>
                ))}
              </div>
            )}
          </div>
        )}
      </nav>
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
        <header className="mas-header sticky top-0 z-30 border-b border-zinc-200/70 bg-white/60 backdrop-blur-xl dark:border-white/[0.06] dark:bg-[#050608]/60">
          <div className="mx-auto flex h-14 max-w-7xl items-center gap-4 px-4 sm:px-6 lg:gap-6">
            <Link href={editor ? "/cms/" : "/"} className="flex shrink-0 items-center gap-2.5">
              <Logo />
              <span className="text-[15px] font-semibold tracking-tight">mas</span>
            </Link>
            <PriorityNav items={nav} pathname={pathname} />
            <div className="ml-auto flex shrink-0 items-center gap-3">
              {!session.totp_enabled && (
                <Link href="/settings/" className="hidden whitespace-nowrap rounded-full border border-amber-400/30 bg-amber-400/10 px-2.5 py-0.5 text-xs text-amber-600 xl:inline dark:text-amber-300">
                  {t("Enable 2FA")}
                </Link>
              )}
              {!editor && <button
                onClick={() => setPalette(true)}
                className="glass hidden h-8 items-center gap-6 whitespace-nowrap rounded-lg px-3 text-xs text-zinc-500 transition hover:text-zinc-800 sm:flex dark:hover:text-zinc-200"
                title={t("Search…")}
              >
                <span className="hidden lg:inline">{t("Search…")}</span>
                <Kbd>⌘K</Kbd>
              </button>}
              <UserMenu session={session} />
            </div>
          </div>
          <nav className="no-scrollbar flex gap-1 overflow-x-auto px-4 pb-2 md:hidden">
            {nav.map((item) => (
              <Link key={item.href} href={item.href} className={`shrink-0 whitespace-nowrap rounded-md px-2.5 py-1 text-xs ${item.match(pathname) ? "bg-zinc-200 dark:bg-white/10" : "text-zinc-500"}`}>
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
