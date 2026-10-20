"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Shell } from "@/components/Shell";
import {
  Badge, Button, ConfirmDialog, EmptyState, ErrorText, Input, Notice, PageHeader, Spinner, Stat, StatusDot,
} from "@/components/ui";
import { api, ApiError, daysUntil, githubUrl, timeAgo, type App, type Service } from "@/lib/api";
import { useT } from "@/lib/i18n";

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

function ServiceCard({ s }: { s: Service }) {
  const t = useT();
  const gh = githubUrl(s.git_remote);
  const ssl = s.ssl_expires ? daysUntil(s.ssl_expires) : null;
  return (
    <Link
      href={`/service/?id=${encodeURIComponent(s.id)}`}
      className="glass group relative flex flex-col gap-4 rounded-2xl p-5 transition duration-300 hover:-translate-y-0.5 hover:border-indigo-400/40 hover:shadow-[0_20px_60px_-25px_rgba(99,102,241,0.6)]"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2.5">
            <StatusDot status={s.status} />
            <h3 className="truncate font-semibold tracking-tight">{s.display_name || s.name}</h3>
          </div>
          <p className="mt-1 truncate pl-5 text-xs text-zinc-500">
            {s.domains.length ? s.domains.join(" · ") : <span className="italic">{t("no domain")}</span>}
          </p>
        </div>
        <span className="text-zinc-400 opacity-0 transition group-hover:translate-x-0.5 group-hover:opacity-100">→</span>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone={TYPE_TONE[s.type] ?? "zinc"}>{s.type}{s.type_source === "manual" ? "*" : ""}</Badge>
        <Badge>{s.manager}</Badge>
        {s.port && <Badge className="mono">:{s.port}</Badge>}
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

  const all = data?.services ?? [];
  const services = useMemo(() => {
    const q = query.trim().toLowerCase();
    return all.filter((s) => {
      if (filter === "domains" && s.domains.length === 0) return false;
      if (filter === "nodomain" && s.domains.length > 0) return false;
      if (filter === "issues" && !hasIssue(s)) return false;
      if (!q) return true;
      return [s.name, s.display_name, s.id, s.type, s.manager, s.path, ...s.domains].some((v) => v && v.toLowerCase().includes(q));
    });
  }, [all, query, filter]);

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
            <Button onClick={() => setConfirmNginx(true)}>{t("Reload nginx")}</Button>
            <Button variant="primary" onClick={rescan} disabled={scanning}>{scanning ? <Spinner /> : null}{t("Rescan")}</Button>
          </>
        }
      />

      <div className="mb-8 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label={t("Services up")} value={<>{running}<span className="text-lg text-zinc-500">/{all.length}</span></>} tone="emerald" />
        <Stat label={t("Apps live")} value={live} hint={<Link href="/apps/" className="hover:underline">{t("{n} deployed from GitHub", { n: apps.length })} →</Link>} tone="violet" />
        <Stat label={t("Need attention")} value={issues} tone={issues ? "red" : "zinc"} hint={t("stopped or certificate < 14 days")} />
        <Stat label={t("Certificates expiring")} value={expiring} tone={expiring ? "amber" : "sky"} hint={<Link href="/health/" className="hover:underline">{t("full health check")} →</Link>} />
      </div>

      <div className="mb-4 space-y-3">
        <Notice>{notice}</Notice>
        <ErrorText>{error ? <span className="whitespace-pre-wrap">{error}</span> : null}</ErrorText>
      </div>

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

      {data && services.length === 0 ? (
        <EmptyState title={t("No service matches")}>{t("Try another filter.")}</EmptyState>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {services.map((s) => <ServiceCard key={s.id} s={s} />)}
          {!data && Array.from({ length: 6 }).map((_, i) => <div key={i} className="glass shimmer h-40 rounded-2xl" />)}
        </div>
      )}

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
