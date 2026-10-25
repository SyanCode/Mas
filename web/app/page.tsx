"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { FolderHeader, FolderLayout, FolderSidebar, MoveMenu, dragProps, useFolderView, useFolders, visibleIds, type FoldersApi } from "@/components/Folders";
import { RestartAll } from "@/components/RestartAll";
import { Shell } from "@/components/Shell";
import {
  Badge, Button, ConfirmDialog, EmptyState, ErrorText, Input, Notice, PageHeader, Spinner, Stat, StatusDot,
} from "@/components/ui";
import { api, ApiError, daysUntil, githubUrl, timeAgo, type App, type Service } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { BENTO_SPAN, bentoSize, useTheme } from "@/lib/theme";
import { useTypeLabels } from "@/lib/typeLabels";

interface ServicesResponse {
  scanned_at: string;
  warnings: string[];
  services: Service[];
}

type Filter = "all" | "domains" | "nodomain" | "issues";

const TYPE_TONE: Record<string, "sky" | "violet" | "emerald" | "amber" | "zinc"> = {
  nextjs: "zinc", node: "emerald", python: "sky", php: "violet", static: "amber", unknown: "zinc",
};

function hasIssue(s: Service) {
  return s.status === "stopped" || s.status === "errored" || (s.ssl_expires !== null && daysUntil(s.ssl_expires) < 14);
}

function ServiceCard({ s, f, size = "sm" }: { s: Service; f: FoldersApi; size?: "lg" | "wide" | "sm" }) {
  const t = useT();
  const gh = githubUrl(s.git_remote);
  const ssl = s.ssl_expires ? daysUntil(s.ssl_expires) : null;
  return (
    <Link
      href={`/service/?id=${encodeURIComponent(s.id)}`}
      {...dragProps("service", s.id)}
      className={`glass group relative flex flex-col gap-4 rounded-2xl p-5 transition duration-300 hover:-translate-y-0.5 hover:border-indigo-400/40 hover:shadow-[0_20px_60px_-25px_var(--color-indigo-500)] ${BENTO_SPAN[size]} ${size === "lg" ? "justify-between" : ""}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2.5">
            <StatusDot status={s.status} />
            <h3 className={`truncate font-semibold tracking-tight ${size === "lg" ? "text-xl" : ""}`}>{s.display_name || s.name}</h3>
          </div>
          <p className="mt-1 truncate pl-5 text-xs text-zinc-500">
            {s.domains.length ? s.domains.join(" · ") : <span className="italic">{t("no domain")}</span>}
          </p>
        </div>
        <MoveMenu f={f} kind="service" id={s.id} className="opacity-0 transition group-hover:opacity-100 [&>button]:bg-zinc-200 [&>button]:text-zinc-700 dark:[&>button]:bg-white/10 dark:[&>button]:text-zinc-200" />
      </div>
      {size === "lg" && s.last_commit && (
        <div className="rounded-xl border border-zinc-200/70 px-3 py-2 text-xs dark:border-white/[0.06]">
          <div className="text-zinc-500">{t("Last commit")}</div>
          <div className="mt-0.5 truncate font-medium">{s.last_commit.subject}</div>
          <div className="mt-0.5 text-zinc-500">{s.last_commit.author}{s.last_commit.date ? ` · ${timeAgo(s.last_commit.date)}` : ""}</div>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone={TYPE_TONE[s.type] ?? "zinc"}>{s.type}{s.type_source === "manual" ? "*" : ""}</Badge>
        <Badge>{s.manager}</Badge>
        {s.port && <Badge className="mono">:{s.port}</Badge>}
        {s.app && <Badge tone="violet">{t("Mas app: {name}", { name: s.app.name })}</Badge>}
      </div>
      <div className="mt-auto flex items-center justify-between gap-2 border-t border-zinc-200/70 pt-3 text-xs text-zinc-500 dark:border-white/[0.06]">
        <span className="truncate">{gh ? gh.replace("https://github.com/", "") : s.path ?? "—"}</span>
        {ssl !== null && (
          <span className={ssl < 0 ? "text-rose-500" : ssl < 14 ? "text-amber-500" : ""}>
            {ssl < 0 ? t("SSL expired") : t("SSL {n}d", { n: ssl })}
          </span>
        )}
      </div>
    </Link>
  );
}

function ServiceRows({ services, f }: { services: Service[]; f: FoldersApi }) {
  const t = useT();
  return (
    <div className="glass overflow-hidden rounded-2xl">
      {services.map((s) => {
        const ssl = s.ssl_expires ? daysUntil(s.ssl_expires) : null;
        return (
          <Link key={s.id} href={`/service/?id=${encodeURIComponent(s.id)}`} {...dragProps("service", s.id)}
            className="group grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 border-b border-zinc-200/60 px-4 py-2.5 text-sm transition last:border-0 hover:bg-zinc-100/70 md:grid-cols-[minmax(0,2fr)_minmax(0,2fr)_9rem_5rem_2rem] dark:border-white/[0.05] dark:hover:bg-white/[0.03]">
            <span className="flex min-w-0 items-center gap-2.5">
              <StatusDot status={s.status} size="sm" />
              <span className="truncate font-medium">{s.display_name || s.name}</span>
            </span>
            <span className="hidden truncate text-xs text-zinc-500 md:block">{s.domains.join(" · ") || s.path || "—"}</span>
            <span className="hidden gap-1 md:flex"><Badge tone={TYPE_TONE[s.type] ?? "zinc"}>{s.type}</Badge><Badge>{s.manager}</Badge></span>
            <span className={`text-xs ${ssl !== null && ssl < 14 ? "text-amber-500" : "text-zinc-500"}`}>{ssl === null ? "" : ssl < 0 ? t("SSL expired") : t("SSL {n}d", { n: ssl })}</span>
            <MoveMenu f={f} kind="service" id={s.id} className="hidden opacity-0 transition group-hover:opacity-100 md:block [&>button]:bg-zinc-200 [&>button]:text-zinc-700 dark:[&>button]:bg-white/10 dark:[&>button]:text-zinc-200" />
          </Link>
        );
      })}
    </div>
  );
}

function Overview() {
  const t = useT();
  const [data, setData] = useState<ServicesResponse | null>(null);
  const [apps, setApps] = useState<App[]>([]);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [scanning, setScanning] = useState(false);
  const [confirmNginx, setConfirmNginx] = useState(false);
  const [notice, setNotice] = useState("");
  const { style } = useTheme();
  const typeLabels = useTypeLabels();
  const f = useFolders();
  const [view, setView] = useFolderView("services");

  const load = useCallback(() => {
    api<ServicesResponse>("/services").then(setData).catch((e: ApiError) => setError(e.detail));
    api<App[]>("/apps").then(setApps).catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 30_000);
    return () => clearInterval(timer);
  }, [load]);

  async function rescan() {
    setScanning(true);
    try {
      await api("/scan", { method: "POST" });
      load();
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setScanning(false);
    }
  }

  async function reloadNginx() {
    setConfirmNginx(false);
    try {
      await api("/nginx/reload", { method: "POST" });
      setNotice(t("nginx configuration is valid and was reloaded."));
    } catch (e) {
      setError((e as ApiError).detail);
    }
  }

  const all = useMemo(() => data?.services ?? [], [data]);
  const allIds = useMemo(() => all.map((s) => s.id), [all]);
  const services = useMemo(() => {
    const q = query.trim().toLowerCase();
    const inView = new Set(q ? allIds : visibleIds(f, "service", allIds, view));
    return all.filter((s) => {
      if (!inView.has(s.id)) return false;
      if (filter === "domains" && s.domains.length === 0) return false;
      if (filter === "nodomain" && s.domains.length > 0) return false;
      if (filter === "issues" && !hasIssue(s)) return false;
      if (!q) return true;
      return [s.name, s.display_name, s.id, s.type, s.manager, s.path, ...s.domains].some((v) => v && v.toLowerCase().includes(q));
    });
  }, [all, allIds, f, view, query, filter]);

  const running = all.filter((s) => s.status === "running").length;
  const issues = all.filter(hasIssue).length;
  const expiring = all.filter((s) => s.ssl_expires && daysUntil(s.ssl_expires) < 14).length;
  const live = apps.filter((a) => a.current).length;
  const counts: Record<Filter, number> = {
    all: all.length,
    domains: all.filter((s) => s.domains.length > 0).length,
    nodomain: all.filter((s) => s.domains.length === 0).length,
    issues,
  };

  return (
    <>
      <PageHeader
        eyebrow={t("Server")}
        title={<>{t("Everything running")} <span className="text-gradient">{t("on this machine")}</span></>}
        subtitle={data ? `${t("Scanned {when}", { when: timeAgo(data.scanned_at) })} · nginx, systemd, PM2, Docker, PHP-FPM` : t("Scanning…")}
        actions={
          <>
            <RestartAll />
            <Button onClick={() => setConfirmNginx(true)}>{t("Reload nginx")}</Button>
            <Button variant="primary" onClick={rescan} disabled={scanning}>{scanning ? <Spinner /> : null}{t("Rescan")}</Button>
          </>
        }
      />

      <div className={`mb-8 grid grid-cols-2 gap-3 lg:grid-cols-4 ${style.layout === "bento" ? "[&>*:first-child]:col-span-2 [&>*:first-child]:row-span-2 [&>*:first-child>.text-3xl]:mt-6 [&>*:first-child>.text-3xl]:text-7xl" : ""}`}>
        <Stat label={t("Services up")} value={<>{running}<span className="text-lg text-zinc-500">/{all.length}</span></>} tone="emerald" />
        <Stat label={t("Apps live")} value={live} hint={<Link href="/apps/" className="hover:underline">{t("{n} deployed from GitHub", { n: apps.length })} →</Link>} tone="violet" />
        <Stat label={t("Need attention")} value={issues} tone={issues ? "red" : "zinc"} hint={t("stopped or certificate < 14 days")} />
        <Stat label={t("Certificates expiring")} value={expiring} tone={expiring ? "amber" : "sky"} hint={<Link href="/health/" className="hover:underline">{t("full health check")} →</Link>} />
      </div>

      <div className="mb-4 space-y-3">
        <Notice>{notice}</Notice>
        <ErrorText>{error ? <span className="whitespace-pre-wrap">{error}</span> : null}</ErrorText>
      </div>

      <FolderLayout sidebar={<FolderSidebar f={f} kind="service" ids={allIds} view={view} onView={setView} typeLabels={typeLabels} />}>
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="glass inline-flex rounded-xl p-1">
          {(["all", "domains", "nodomain", "issues"] as Filter[]).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`rounded-lg px-3 py-1.5 text-xs transition ${filter === f ? "bg-zinc-900 text-white shadow dark:bg-white dark:text-zinc-900" : "text-zinc-500 hover:text-zinc-900 dark:hover:text-white"}`}
            >
              {{ all: t("All"), domains: t("With domain"), nodomain: t("No domain"), issues: t("Issues") }[f]} <span className="opacity-60">{counts[f]}</span>
            </button>
          ))}
        </div>
        <Input placeholder={t("Filter by name, domain, path…")} value={query} onChange={(e) => setQuery(e.target.value)} className="sm:ml-auto sm:max-w-xs" />
      </div>

      {!query && <FolderHeader f={f} kind="service" ids={allIds} view={view} onView={setView} />}
      {data && services.length === 0 ? (
        <EmptyState title={t("No service matches")}>{view === "all" || query ? t("Try another filter.") : t("Drag services onto this folder in the list, or use the folder button of a card.")}</EmptyState>
      ) : style.layout === "list" && data ? (
        <ServiceRows services={services} f={f} />
      ) : (
        <div className={`grid gap-4 sm:grid-cols-2 ${style.layout === "bento" ? "grid-flow-dense xl:grid-cols-4" : "xl:grid-cols-3"}`}>
          {services.map((s, i) => <ServiceCard key={s.id} s={s} f={f} size={style.layout === "bento" ? bentoSize(i, services.length) : "sm"} />)}
          {!data && Array.from({ length: 6 }).map((_, i) => <div key={i} className="glass shimmer h-40 rounded-2xl" />)}
        </div>
      )}

      </FolderLayout>

      {data && data.warnings.length > 0 && (
        <details className="mt-8 text-sm text-zinc-500">
          <summary className="cursor-pointer">{t("{n} scan warning(s)", { n: data.warnings.length })}</summary>
          <ul className="mt-2 list-disc space-y-1 pl-5">{data.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        </details>
      )}

      <ConfirmDialog
        open={confirmNginx}
        title={t("Reload nginx?")}
        message={t("Mas runs `nginx -t` first and only reloads if the configuration is valid.")}
        confirmLabel={t("Reload")}
        onConfirm={reloadNginx}
        onCancel={() => setConfirmNginx(false)}
      />
    </>
  );
}

export default function OverviewPage() {
  return (
    <Shell>
      <Overview />
    </Shell>
  );
}
