"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { FolderHeader, FolderLayout, FolderSidebar, MoveMenu, dragProps, useFolderView, useFolders, visibleIds, type FoldersApi } from "@/components/Folders";
import { Shell } from "@/components/Shell";
import { Actions, Badge, Button, ConfirmDialog, EmptyState, ErrorText, Input, Monogram, Notice, PageHeader, ReleaseBadge, StatusDot } from "@/components/ui";
import { api, ApiError, timeAgo, type App, type FreezeState, type GitHubStatus } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { BENTO_SPAN, bentoSize, useTheme } from "@/lib/theme";
import { useTypeLabels } from "@/lib/typeLabels";

function hue(name: string) {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return h % 360;
}

function GitHubMark() {
  return <svg viewBox="0 0 16 16" className="mr-1 inline h-3 w-3 align-[-2px]" fill="currentColor" aria-hidden><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" /></svg>;
}

/** Frozen, commits waiting, project: what the cards and rows show beside the type. */
function AppFlags({ a, frozenAll }: { a: App; frozenAll: boolean }) {
  const t = useT();
  if (a.mode === "stack") return null;
  return (
    <>
      {(!a.auto_deploy || frozenAll) && <span title={t("Pushes do not deploy this app")}><Badge tone="sky">❄ {t("frozen")}</Badge></span>}
      {a.pending && <span title={a.pending.message}><Badge tone="amber">{t("{n} commit(s) waiting", { n: a.pending.commits })}</Badge></span>}
      {a.project && <Badge tone="violet">◆ {a.project}</Badge>}
    </>
  );
}

function Banner({ a, f, tall = false }: { a: App; f: FoldersApi; tall?: boolean }) {
  const t = useT();
  const { style } = useTheme();
  const last = a.last_release;
  const h = hue(a.id);
  if (!style.banner) {
    return (
      <div className="flex items-center gap-3 px-5 pt-5">
        <Monogram name={a.id} />
        <span className="min-w-0 flex-1 truncate text-sm text-zinc-500">{a.domain ?? t("no domain yet")}</span>
        {last && <ReleaseBadge status={last.status} />}
        <MoveMenu f={f} kind="app" id={a.id} className="opacity-0 transition group-hover:opacity-100 [&>button]:bg-zinc-200 [&>button]:text-zinc-700 dark:[&>button]:bg-white/10 dark:[&>button]:text-zinc-200" />
      </div>
    );
  }
  return (
    <div className={`relative overflow-hidden border-b border-white/[0.06] ${tall ? "h-full min-h-40" : "h-28"}`}
      style={{ background: `radial-gradient(120% 120% at 0% 0%, hsl(${h} 90% 60% / .55), transparent 55%), radial-gradient(120% 120% at 100% 100%, hsl(${(h + 70) % 360} 90% 55% / .45), transparent 55%), #0a0b10` }}>
      <div className="absolute inset-0 opacity-30 [background-image:linear-gradient(rgba(255,255,255,.08)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,.08)_1px,transparent_1px)] [background-size:22px_22px]" />
      <div className="absolute bottom-3 left-4 flex items-center gap-3">
        <Monogram name={a.id} size={tall ? "lg" : "md"} />
        <span className={`font-medium text-white/90 drop-shadow ${tall ? "text-lg" : "text-sm"}`}>{a.domain ?? t("no domain yet")}</span>
      </div>
      <div className="absolute right-3 top-3 flex items-center gap-2">
        <MoveMenu f={f} kind="app" id={a.id} className="opacity-0 transition group-hover:opacity-100" />
        {last && <ReleaseBadge status={last.status} />}
      </div>
    </div>
  );
}

function CardBody({ a, frozenAll }: { a: App; frozenAll: boolean }) {
  const t = useT();
  const last = a.last_release;
  return (
    <div className="flex flex-1 flex-col gap-3 p-5">
      <div className="flex items-center justify-between gap-2">
        <h3 className="truncate font-semibold tracking-tight">{a.id}</h3>
        <div className="flex shrink-0 gap-1.5">
          {a.mode === "stack" ? <Badge tone="emerald">{t("service")}</Badge> : <Badge tone="sky">{a.type}</Badge>}
          {a.mode === "adopted" && <Badge tone="violet">{t("adopted")}</Badge>}
        </div>
      </div>
      {a.mode === "stack" ? <p className="truncate text-xs text-zinc-500">{a.name}</p> : <p className="truncate text-xs text-zinc-500"><GitHubMark />{a.repo} · {a.branch}</p>}
      <div className="flex flex-wrap gap-1.5 empty:hidden"><AppFlags a={a} frozenAll={frozenAll} /></div>
      <div className="mt-auto flex items-center gap-2 border-t border-zinc-200/70 pt-3 text-xs text-zinc-500 dark:border-white/[0.06]">
        {last ? (
          <>
            <StatusDot status={last.status} size="sm" />
            <span className="truncate">{last.message || last.trigger}</span>
            <span className="ml-auto shrink-0">{timeAgo(last.created_at)}</span>
          </>
        ) : (
          <span>{t("Never deployed")}</span>
        )}
      </div>
    </div>
  );
}

const CARD = "glass group flex flex-col overflow-hidden rounded-2xl transition duration-300 hover:-translate-y-0.5 hover:border-indigo-400/40 hover:shadow-[0_24px_70px_-30px_var(--color-indigo-500)]";

function AppCard({ a, f, frozenAll }: { a: App; f: FoldersApi; frozenAll: boolean }) {
  return (
    <Link href={`/apps/view/?id=${encodeURIComponent(a.id)}`} {...dragProps("app", a.id)} className={CARD}>
      <Banner a={a} f={f} />
      <CardBody a={a} frozenAll={frozenAll} />
    </Link>
  );
}

function AppTile({ a, f, frozenAll, size }: { a: App; f: FoldersApi; frozenAll: boolean; size: "lg" | "wide" | "sm" }) {
  const t = useT();
  if (size === "sm") return <AppCard a={a} f={f} frozenAll={frozenAll} />;
  return (
    <Link href={`/apps/view/?id=${encodeURIComponent(a.id)}`} {...dragProps("app", a.id)} className={`${CARD} ${BENTO_SPAN[size]} ${size === "wide" ? "sm:flex-row" : ""}`}>
      <div className={size === "wide" ? "sm:w-1/2" : "flex-1"}><Banner a={a} f={f} tall /></div>
      <div className={`flex flex-col ${size === "wide" ? "sm:w-1/2" : ""}`}>
        <CardBody a={a} frozenAll={frozenAll} />
        {size === "lg" && a.current && (
          <div className="mx-5 mb-5 rounded-xl border border-zinc-200/70 px-3 py-2 text-xs dark:border-white/[0.06]">
            <div className="text-zinc-500">{t("Live release")}</div>
            <div className="mt-0.5 truncate font-medium">#{a.current.id} · {a.current.message || a.current.trigger}</div>
            <div className="mono mt-0.5 text-zinc-500">{a.current.sha?.slice(0, 7)} · {timeAgo(a.current.created_at)}</div>
          </div>
        )}
      </div>
    </Link>
  );
}

function AppRows({ apps, f, frozenAll }: { apps: App[]; f: FoldersApi; frozenAll: boolean }) {
  const t = useT();
  return (
    <div className="glass overflow-hidden rounded-2xl">
      <div className="hidden grid-cols-[minmax(0,2fr)_minmax(0,2fr)_minmax(0,1.4fr)_7rem_2rem] gap-4 border-b border-zinc-200/80 px-4 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-500 md:grid dark:border-white/[0.06]">
        <span>{t("App")}</span><span>{t("Source")}</span><span>{t("Last deployment")}</span><span>{t("Status")}</span><span />
      </div>
      {apps.map((a) => {
        const last = a.last_release;
        return (
          <Link key={a.id} href={`/apps/view/?id=${encodeURIComponent(a.id)}`} {...dragProps("app", a.id)}
            className="group grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 border-b border-zinc-200/60 px-4 py-3 text-sm transition last:border-0 hover:bg-zinc-100/70 md:grid-cols-[minmax(0,2fr)_minmax(0,2fr)_minmax(0,1.4fr)_7rem_2rem] dark:border-white/[0.05] dark:hover:bg-white/[0.03]">
            <span className="flex min-w-0 items-center gap-2.5">
              <StatusDot status={last?.status ?? "unknown"} size="sm" />
              <span className="min-w-0">
                <span className="block truncate font-medium">{a.id}</span>
                <span className="block truncate text-xs text-zinc-500">{a.domain ?? t("no domain yet")}</span>
                <span className="mt-1 flex flex-wrap gap-1 empty:hidden md:hidden"><AppFlags a={a} frozenAll={frozenAll} /></span>
              </span>
            </span>
            <span className="hidden min-w-0 md:block">
              <span className="block truncate text-xs text-zinc-500">{a.mode === "stack" ? a.name : <><GitHubMark />{a.repo} · {a.branch}</>}</span>
              <span className="mt-1 flex flex-wrap gap-1 empty:hidden"><AppFlags a={a} frozenAll={frozenAll} /></span>
            </span>
            <span className="hidden min-w-0 truncate text-xs text-zinc-500 md:block">{last ? `${last.message || last.trigger} · ${timeAgo(last.created_at)}` : t("Never deployed")}</span>
            <span>{last ? <ReleaseBadge status={last.status} /> : <Badge>{a.mode === "stack" ? t("service") : a.type}</Badge>}</span>
            <MoveMenu f={f} kind="app" id={a.id} className="hidden opacity-0 transition group-hover:opacity-100 md:block [&>button]:bg-zinc-200 [&>button]:text-zinc-700 dark:[&>button]:bg-white/10 dark:[&>button]:text-zinc-200" />
          </Link>
        );
      })}
    </div>
  );
}

function Apps() {
  const t = useT();
  const { style } = useTheme();
  const typeLabels = useTypeLabels();
  const [apps, setApps] = useState<App[] | null>(null);
  const [github, setGithub] = useState<GitHubStatus | null>(null);
  const [freeze, setFreeze] = useState<FreezeState | null>(null);
  const [error, setError] = useState("");
  const [confirmAll, setConfirmAll] = useState(false);
  const [confirmFreeze, setConfirmFreeze] = useState(false);
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const f = useFolders();
  const [view, setView] = useFolderView("apps");

  const load = useCallback(() => {
    api<App[]>("/apps").then(setApps).catch((e: ApiError) => setError(e.detail));
    api<FreezeState>("/deploy/freeze").then(setFreeze).catch(() => {});
  }, []);

  useEffect(() => {
    load();
    api<GitHubStatus>("/github/status").then(setGithub).catch(() => {});
    const timer = setInterval(load, 8_000);
    return () => clearInterval(timer);
  }, [load]);

  async function deployAll() {
    setConfirmAll(false);
    setError("");
    try {
      const r = await api<{ queued: { app_id: string }[] }>("/apps/deploy-all", { method: "POST" });
      setNotice(r.queued.length ? t("{n} deployment(s) queued: they run one after the other.", { n: r.queued.length }) : t("Nothing to deploy: every app is already deploying."));
      load();
    } catch (e) {
      setError((e as ApiError).detail);
    }
  }

  async function setFrozenAll(frozen: boolean) {
    setConfirmFreeze(false);
    try {
      setFreeze(await api<FreezeState>("/deploy/freeze", { method: "PUT", body: { frozen } }));
      setNotice(frozen ? t("Deployments frozen: pushes are recorded and nothing builds until you resume.") : t("Deployments resumed: the next push deploys again."));
      load();
    } catch (e) {
      setError((e as ApiError).detail);
    }
  }

  async function freezeApps(ids: string[], frozen: boolean) {
    const fromGithub = ids.filter((id) => apps?.some((a) => a.id === id && a.mode !== "stack"));
    if (!fromGithub.length) {
      setNotice(t("No app deployed from GitHub in this folder."));
      return;
    }
    try {
      const r = await api<{ changed: string[] }>("/apps/freeze", { method: "POST", body: { app_ids: fromGithub, frozen } });
      setNotice(frozen ? t("{n} app(s) frozen.", { n: r.changed.length }) : t("{n} app(s) unfrozen.", { n: r.changed.length }));
      load();
    } catch (e) {
      setError((e as ApiError).detail);
    }
  }

  const allIds = useMemo(() => (apps ?? []).map((a) => a.id), [apps]);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const ids = new Set(q ? allIds : visibleIds(f, "app", allIds, view));
    return (apps ?? []).filter((a) => ids.has(a.id) && (!q || [a.id, a.name, a.repo, a.domain, a.type, a.project].some((v) => v && v.toLowerCase().includes(q))));
  }, [apps, allIds, f, view, query]);

  const frozenAll = !!freeze?.frozen;
  const newButton = github?.configured ? (
    <Link href="/apps/new/" className="mas-cta inline-flex h-9 items-center gap-2 rounded-lg px-4 text-sm font-medium text-white shadow-[0_0_30px_-8px_var(--color-indigo-500)] transition hover:brightness-110">
      <span className="text-base leading-none">+</span> {t("New app")}
    </Link>
  ) : null;
  const fromGithub = (apps ?? []).filter((a) => a.mode !== "stack" && a.auto_deploy).length;
  const deployAllButton = fromGithub > 1 ? (
    <Button onClick={() => setConfirmAll(true)} className="!h-9" title={t("Deploy the latest commit of every app from GitHub")}>{t("Redeploy all")}</Button>
  ) : null;
  const linkButton = "glass inline-flex h-9 items-center gap-2 rounded-lg px-4 text-sm font-medium transition hover:bg-white/[0.08]";
  const waiting = (apps ?? []).filter((a) => a.pending).length;

  return (
    <>
      <PageHeader eyebrow={t("Deployments")} title={t("Apps")} subtitle={t("Push to GitHub, Mas builds and ships it. Atomic releases, instant rollback.")} actions={
        <Actions main={newButton} more={[
          { label: t("Projects"), href: "/projects/" },
          { label: t("Deploy a service"), href: "/apps/services/" },
          { label: frozenAll ? t("▶ Resume deployments") : t("❄ Freeze deployments"), onClick: () => (frozenAll ? setFrozenAll(false) : setConfirmFreeze(true)), disabled: !freeze },
          { label: t("Redeploy all"), onClick: () => setConfirmAll(true), disabled: !deployAllButton },
          { label: t("Maintenance"), href: "/maintenance/" },
        ]}>
          <Link href="/maintenance/" className="inline-flex h-9 items-center rounded-lg border border-zinc-300 px-3 text-sm hover:bg-zinc-100 dark:border-white/10 dark:hover:bg-white/[0.06]">{t("Maintenance")}</Link>
          {freeze && <Button onClick={() => (frozenAll ? setFrozenAll(false) : setConfirmFreeze(true))} className="!h-9" title={t("Pushes are recorded but nothing builds")}>
            {frozenAll ? t("▶ Resume deployments") : t("❄ Freeze deployments")}
          </Button>}
          {deployAllButton}
          <Link href="/projects/" className={linkButton}>{t("Projects")}</Link>
          <Link href="/apps/services/" className={linkButton}>{t("Deploy a service")}</Link>
          {newButton}
        </Actions>
      } />
      <div className="mb-4 space-y-3">
        <ErrorText>{error}</ErrorText>
        <Notice>{notice}</Notice>
        {frozenAll && (
          <div className="flex flex-wrap items-center gap-3 rounded-xl border border-sky-400/30 bg-sky-500/10 px-4 py-3 text-sm text-sky-800 dark:text-sky-200">
            <span className="text-lg">❄</span>
            <span className="min-w-[14rem] flex-1">{t("Every deployment is frozen: pushes to GitHub are recorded, nothing builds.")} {waiting > 0 && t("{n} app(s) have commits waiting.", { n: waiting })}</span>
            <Button onClick={() => setFrozenAll(false)}>{t("Resume")}</Button>
          </div>
        )}
      </div>
      <ConfirmDialog
        open={confirmAll}
        title={t("Redeploy all apps?")}
        message={t("The latest commit of each of the {n} apps from GitHub is built and deployed, one after the other. Ready-made services and frozen apps are not touched.", { n: fromGithub })}
        confirmLabel={t("Redeploy all")}
        onConfirm={deployAll}
        onCancel={() => setConfirmAll(false)}
      />
      <ConfirmDialog
        open={confirmFreeze}
        title={t("Freeze every deployment?")}
        message={t("Pushes to GitHub stop deploying: Mas records them and shows the commits waiting on each app. You can still deploy an app by hand, and resume at any time.")}
        confirmLabel={t("Freeze deployments")}
        onConfirm={() => setFrozenAll(true)}
        onCancel={() => setConfirmFreeze(false)}
      />
      {github && !github.configured && (
        <EmptyState title={t("Connect GitHub to start deploying")} action={<Link href="/settings/" className="text-sm font-medium text-indigo-500 hover:underline">{t("Settings")} → GitHub →</Link>}>
          {t("One click creates a private GitHub App on your account; then pick a repository and Mas deploys every push.")}
        </EmptyState>
      )}
      {apps && apps.length === 0 && github?.configured ? (
        <EmptyState title={t("No app yet")} action={newButton}>
          {t("Deploy a repository, or open a detected service with a GitHub remote and click “Connect to GitHub”.")}
        </EmptyState>
      ) : (
        <FolderLayout sidebar={
          <FolderSidebar f={f} kind="app" ids={allIds} view={view} onView={setView} typeLabels={typeLabels} actions={[
            { label: t("Freeze the apps of this folder"), onClick: (_, ids) => freezeApps(ids, true) },
            { label: t("Unfreeze the apps of this folder"), onClick: (_, ids) => freezeApps(ids, false) },
          ]} />
        }>
          <div className="mb-4 flex items-center gap-3">
            <Input placeholder={t("Search apps…")} value={query} onChange={(e) => setQuery(e.target.value)} className="sm:max-w-xs" />
            {query && <span className="text-xs text-zinc-500">{t("searching every folder")}</span>}
          </div>
          {!query && <FolderHeader f={f} kind="app" ids={allIds} view={view} onView={setView} />}
          {apps && shown.length === 0 && (
            <EmptyState title={query ? t("No app matches") : t("Nothing here yet")}>
              {query ? t("Try another search.") : t("Drag apps onto this folder in the list, or use the folder button of a card.")}
            </EmptyState>
          )}
          {style.layout === "list" && shown.length > 0 ? (
            <AppRows apps={shown} f={f} frozenAll={frozenAll} />
          ) : style.layout === "bento" ? (
            <div className="grid grid-flow-dense gap-4 sm:grid-cols-2 xl:grid-cols-4">
              {shown.map((a, i) => <AppTile key={a.id} a={a} f={f} frozenAll={frozenAll} size={bentoSize(i, shown.length)} />)}
              {!apps && !error && Array.from({ length: 4 }).map((_, i) => <div key={i} className={`glass shimmer h-64 rounded-2xl ${i === 0 ? BENTO_SPAN.lg : ""}`} />)}
            </div>
          ) : (
            <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
              {shown.map((a) => <AppCard key={a.id} a={a} f={f} frozenAll={frozenAll} />)}
              {!apps && !error && Array.from({ length: 3 }).map((_, i) => <div key={i} className="glass shimmer h-64 rounded-2xl" />)}
            </div>
          )}
        </FolderLayout>
      )}
    </>
  );
}

export default function AppsPage() {
  return (
    <Shell>
      <Apps />
    </Shell>
  );
}
