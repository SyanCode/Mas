"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { createContext, Suspense, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Activity, BarChart3, Bell, Cookie, HeartPulse, ShieldCheck, ExternalLink, FileText, Image as ImageIcon, LayoutDashboard, LogOut, Menu, Moon, Newspaper,
  Server, Settings as SettingsIcon, Sun, Users, X,
} from "lucide-react";
import { Logo } from "@/components/Shell";
import { Avatar } from "@/components/cms/ui/avatar";
import { Button } from "@/components/cms/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/cms/ui/dropdown-menu";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/cms/ui/select";
import { Skeleton } from "@/components/cms/ui/skeleton";
import { Toaster } from "sonner";
import { api, rememberSession, type CmsOverview, type CmsSite, type Session } from "@/lib/api";
import { LanguagePicker, useT } from "@/lib/i18n";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

interface CmsContextValue {
  session: Session;
  sites: CmsSite[];
  site: CmsSite | null;
  overview: CmsOverview | null;
  reloadOverview: () => void;
}
const CmsContext = createContext<CmsContextValue | null>(null);
export function useCms() {
  const ctx = useContext(CmsContext);
  if (!ctx) throw new Error("useCms outside CmsLayout");
  return ctx;
}

const SITE_KEY = "mas-cms-site";

function NavLink({ href, active, icon: Icon, children, count }: { href: string; active: boolean; icon: typeof FileText; children: ReactNode; count?: number }) {
  return (
    <Link href={href}
      className={cn("flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
        active ? "bg-sidebar-accent font-medium text-white" : "text-sidebar-foreground hover:bg-sidebar-accent/60 hover:text-white")}>
      <Icon className="size-4 shrink-0 opacity-80" />
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {count != null && <span className="text-xs text-sidebar-foreground/70">{count}</span>}
    </Link>
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="space-y-0.5">
      <div className="px-3 pb-1 pt-4 text-[11px] font-medium uppercase tracking-wider text-sidebar-foreground/50">{title}</div>
      {children}
    </div>
  );
}

function Sidebar({ onNavigate }: { onNavigate: () => void }) {
  const t = useT();
  const { session, sites, site, overview } = useCms();
  const pathname = usePathname();
  const params = useSearchParams();
  const q = site ? `app=${encodeURIComponent(site.id)}` : "";
  const here = (p: string) => pathname.startsWith(p);
  const collection = params.get("c");
  const page = params.get("p");

  return (
    <div className="flex h-full flex-col bg-sidebar text-sidebar-foreground" onClick={(e) => { if ((e.target as HTMLElement).closest("a")) onNavigate(); }}>
      <div className="flex h-14 items-center gap-2.5 border-b border-sidebar-border px-4">
        <Logo className="h-6 w-6" />
        <span className="text-[15px] font-semibold tracking-tight text-white">mas</span>
        <span className="rounded bg-sidebar-accent px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-sidebar-foreground">CMS</span>
      </div>
      <nav className="flex-1 overflow-y-auto px-2 pb-4">
        <div className="pt-3">
          <NavLink href="/cms/" active={pathname === "/cms/" || pathname === "/cms"} icon={LayoutDashboard}>{t("Dashboard")}</NavLink>
        </div>
        {site && overview && (
          <>
            {overview.collections.length > 0 && (
              <Group title={t("Content")}>
                {overview.collections.map((c) => (
                  <NavLink key={c.id} href={`/cms/content/?${q}&c=${encodeURIComponent(c.id)}`} icon={Newspaper}
                    active={(here("/cms/content") || here("/cms/entry")) && collection === c.id} count={c.total}>{c.label}</NavLink>
                ))}
              </Group>
            )}
            {overview.pages.length > 0 && (
              <Group title={t("Pages")}>
                {overview.pages.map((p) => (
                  <NavLink key={p.id} href={`/cms/pages/?${q}&p=${encodeURIComponent(p.id)}`} icon={FileText}
                    active={here("/cms/pages") && page === p.id}>{p.label}</NavLink>
                ))}
              </Group>
            )}
            <Group title={t("Site")}>
              <NavLink href={`/cms/media/?${q}`} icon={ImageIcon} active={here("/cms/media")}>{t("Media")}</NavLink>
              {site.domain && <NavLink href={`/cms/stats/?${q}`} icon={BarChart3} active={here("/cms/stats")}>{t("Statistics")}</NavLink>}
              {site.domain && <NavLink href={`/cms/uptime/?${q}`} icon={HeartPulse} active={here("/cms/uptime")}>{t("Availability")}</NavLink>}
              <NavLink href={`/cms/compliance/?${q}`} icon={ShieldCheck} active={here("/cms/compliance")}>{t("Compliance")}</NavLink>
              <NavLink href={`/cms/cookies/?${q}`} icon={Cookie} active={here("/cms/cookies")}>{t("Cookie banner")}</NavLink>
              <NavLink href={`/cms/activity/?${q}`} icon={Activity} active={here("/cms/activity")}>{t("History")}</NavLink>
            </Group>
          </>
        )}
        {site && !overview && <div className="space-y-2 px-3 pt-6"><Skeleton className="h-4 bg-sidebar-accent" /><Skeleton className="h-4 bg-sidebar-accent" /><Skeleton className="h-4 w-2/3 bg-sidebar-accent" /></div>}
        {session.role === "admin" && (
          <Group title={t("Administration")}>
            <NavLink href="/cms/users/" icon={Users} active={here("/cms/users")}>{t("Users")}</NavLink>
            <NavLink href={`/cms/notifications/?${q}`} icon={Bell} active={here("/cms/notifications")}>{t("Notifications")}</NavLink>
          </Group>
        )}
      </nav>
      <div className="border-t border-sidebar-border p-2">
        {session.role === "admin"
          ? <NavLink href="/" icon={Server} active={false}>{t("Server dashboard")}</NavLink>
          : <NavLink href="/settings/" icon={SettingsIcon} active={false}>{t("Account")}</NavLink>}
      </div>
      {sites.length === 0 && null}
    </div>
  );
}

function Topbar({ onMenu }: { onMenu: () => void }) {
  const t = useT();
  const { session, sites, site } = useCms();
  const { setMode } = useTheme();
  const pathname = usePathname();
  const [dark, setDark] = useState(false);
  useEffect(() => setDark(document.documentElement.classList.contains("dark")), []);

  function switchSite(id: string) {
    try { localStorage.setItem(SITE_KEY, id); } catch {}
    const next = new URLSearchParams(window.location.search);
    next.set("app", id);
    next.delete("c"); next.delete("p"); next.delete("slug");
    const base = pathname.startsWith("/cms/entry") ? "/cms/" : pathname;
    window.location.href = `${base}?${next.toString()}`;
  }

  async function logout() {
    await api("/logout", { method: "POST" }).catch(() => {});
    window.location.href = "/login/";
  }

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b bg-card px-4">
      <Button variant="ghost" size="icon" className="lg:hidden" onClick={onMenu} aria-label={t("Menu")}><Menu /></Button>
      {sites.length > 0 && site && (
        <Select value={site.id} onValueChange={switchSite}>
          <SelectTrigger className="h-8 w-auto min-w-44 max-w-64 gap-2 text-[13px] font-medium"><SelectValue /></SelectTrigger>
          <SelectContent>{sites.map((s) => <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>)}</SelectContent>
        </Select>
      )}
      {site?.url && (
        <a href={site.url} target="_blank" rel="noreferrer"
          className="hidden items-center gap-1.5 text-[13px] text-muted-foreground hover:text-foreground sm:inline-flex">
          <ExternalLink className="size-3.5" />{t("View site")}
        </a>
      )}
      <div className="ml-auto flex items-center gap-1">
        <LanguagePicker className="hidden h-8 rounded-md border border-input bg-background px-2 text-xs sm:block" />
        <Button variant="ghost" size="icon" aria-label={t("Theme")}
          onClick={() => { const next = !dark; setDark(next); setMode(next ? "dark" : "light"); }}>
          {dark ? <Sun /> : <Moon />}
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><Avatar name={session.username} /></button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel>
              <div>{session.username}</div>
              <div className="text-xs font-normal text-muted-foreground">{session.role === "admin" ? t("Administrator") : t("Editor")}</div>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild><Link href="/settings/"><SettingsIcon />{t("Account")}</Link></DropdownMenuItem>
            <DropdownMenuItem onSelect={logout} destructive><LogOut />{t("Log out")}</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}

function Frame({ children }: { children: ReactNode }) {
  const t = useT();
  const params = useSearchParams();
  const [session, setSession] = useState<Session | null>(null);
  const [sites, setSites] = useState<CmsSite[] | null>(null);
  const [overview, setOverview] = useState<CmsOverview | null>(null);
  const [overviewKey, setOverviewKey] = useState(0);
  const [open, setOpen] = useState(false);
  const wanted = params.get("app");

  useEffect(() => {
    api<Session>("/session").then((s) => { rememberSession(s); setSession(s); }).catch(() => { window.location.href = "/login/"; });
    api<CmsSite[]>("/cms/sites").then(setSites).catch(() => setSites([]));
  }, []);

  const site = useMemo(() => {
    if (!sites?.length) return null;
    let remembered: string | null = null;
    try { remembered = localStorage.getItem(SITE_KEY); } catch {}
    return sites.find((s) => s.id === wanted) ?? sites.find((s) => s.id === remembered) ?? sites[0];
  }, [sites, wanted]);

  useEffect(() => {
    if (!site) return setOverview(null);
    setOverview(null);
    api<CmsOverview>(`/cms/sites/${encodeURIComponent(site.id)}/overview`).then(setOverview).catch(() => setOverview({ pages: [], collections: [] }));
  }, [site, overviewKey]);

  if (!session || sites === null) {
    return <div className="cms cms-page flex min-h-screen items-center justify-center"><Logo className="h-10 w-10 animate-pulse" /></div>;
  }
  const ctx: CmsContextValue = { session, sites, site, overview, reloadOverview: () => setOverviewKey((k) => k + 1) };
  return (
    <CmsContext.Provider value={ctx}>
      <div className="cms cms-page min-h-screen text-sm">
        <aside className="fixed inset-y-0 left-0 z-40 hidden w-60 lg:block"><Sidebar onNavigate={() => {}} /></aside>
        {open && (
          <div className="fixed inset-0 z-50 lg:hidden">
            <div className="absolute inset-0 bg-black/60" onClick={() => setOpen(false)} />
            <aside className="absolute inset-y-0 left-0 w-64">
              <Sidebar onNavigate={() => setOpen(false)} />
              <Button variant="ghost" size="icon" className="absolute right-2 top-2 text-white hover:bg-sidebar-accent hover:text-white" onClick={() => setOpen(false)} aria-label={t("Close")}><X /></Button>
            </aside>
          </div>
        )}
        <div className="lg:pl-60">
          <Topbar onMenu={() => setOpen(true)} />
          <main className="mx-auto max-w-6xl px-4 py-6 sm:px-6">{children}</main>
        </div>
        <Toaster position="bottom-right" richColors closeButton />
      </div>
    </CmsContext.Provider>
  );
}

/** The CMS back-office frame: dark sidebar, top bar, content. Every /cms page renders inside it. */
export function CmsLayout({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<div className="cms cms-page min-h-screen" />}>
      <Frame>{children}</Frame>
    </Suspense>
  );
}

export function PageTitle({ title, description, actions }: { title: ReactNode; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h1 className="truncate text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

/** Shown when the account has no site yet. */
export function NoSite() {
  const t = useT();
  const { session } = useCms();
  return (
    <div className="rounded-lg border border-dashed p-10 text-center">
      <p className="font-medium">{t("No site yet")}</p>
      <p className="mt-1 text-sm text-muted-foreground">
        {session.role === "admin" ? t("Link an app to GitHub first.") : t("Your administrator has not given you access to a site yet.")}
      </p>
    </div>
  );
}
