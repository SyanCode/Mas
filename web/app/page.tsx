"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Shell } from "@/components/Shell";
import { Badge, Button, ConfirmDialog, ErrorText, Input, StatusDot } from "@/components/ui";
import { api, ApiError, daysUntil, githubUrl, timeAgo, type Service } from "@/lib/api";

interface ServicesResponse {
  scanned_at: string;
  warnings: string[];
  services: Service[];
}

type Filter = "all" | "domains" | "nodomain" | "issues";

function hasIssue(s: Service) {
  return s.status === "stopped" || s.status === "errored" || (s.ssl_expires !== null && daysUntil(s.ssl_expires) < 14);
}

function SslBadge({ iso }: { iso: string | null }) {
  if (!iso) return null;
  const days = daysUntil(iso);
  if (days < 0) return <Badge tone="red">SSL expired</Badge>;
  if (days < 14) return <Badge tone="amber">SSL {days}d</Badge>;
  return <span className="text-xs text-zinc-500">SSL {days}d</span>;
}

function ServiceRow({ s }: { s: Service }) {
  const gh = githubUrl(s.git_remote);
  return (
    <li className="group relative flex flex-col gap-2 px-4 py-3 hover:bg-zinc-50 sm:flex-row sm:items-center sm:gap-4 dark:hover:bg-zinc-800/40">
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <StatusDot status={s.status} />
        <div className="min-w-0">
          <Link href={`/service/?id=${encodeURIComponent(s.id)}`} className="font-medium after:absolute after:inset-0 hover:underline">
            {s.display_name || s.name}
          </Link>
          <div className="flex flex-wrap gap-x-3 text-xs text-zinc-500">
            {s.domains.length === 0 && <span className="italic">no domain</span>}
            {s.domains.slice(0, 3).map((d) => (
              <a key={d} href={`https://${d}`} target="_blank" rel="noreferrer" className="relative z-[1] hover:text-zinc-900 hover:underline dark:hover:text-zinc-100">
                {d} ↗
              </a>
            ))}
            {s.domains.length > 3 && <span>+{s.domains.length - 3}</span>}
          </div>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 pl-5.5 sm:pl-0">
        <Badge tone={s.type === "unknown" ? "zinc" : "sky"}>
          {s.type}
          {s.type_source === "manual" ? "*" : ""}
        </Badge>
        <Badge>{s.manager}</Badge>
        {s.port && <span className="mono text-xs text-zinc-500">:{s.port}</span>}
        <SslBadge iso={s.ssl_expires} />
        {gh && (
          <a href={gh} target="_blank" rel="noreferrer" className="relative z-[1] text-xs text-zinc-500 hover:underline">
            {gh.replace("https://github.com/", "")}
          </a>
        )}
      </div>
    </li>
  );
}

export default function ServicesPage() {
  const [data, setData] = useState<ServicesResponse | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [scanning, setScanning] = useState(false);
  const [confirmNginx, setConfirmNginx] = useState(false);
  const [notice, setNotice] = useState("");

  const load = useCallback(() => {
    api<ServicesResponse>("/services").then(setData).catch((e: ApiError) => setError(e.detail));
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
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
      setNotice("nginx configuration is valid and was reloaded.");
    } catch (e) {
      setError((e as ApiError).detail);
    }
  }

  const services = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (data?.services ?? []).filter((s) => {
      if (filter === "domains" && s.domains.length === 0) return false;
      if (filter === "nodomain" && s.domains.length > 0) return false;
      if (filter === "issues" && !hasIssue(s)) return false;
      if (!q) return true;
      return [s.name, s.display_name, s.id, s.type, s.manager, s.path, ...s.domains]
        .some((v) => v && v.toLowerCase().includes(q));
    });
  }, [data, query, filter]);

  const counts = useMemo(() => {
    const all = data?.services ?? [];
    return {
      all: all.length,
      domains: all.filter((s) => s.domains.length > 0).length,
      nodomain: all.filter((s) => s.domains.length === 0).length,
      issues: all.filter(hasIssue).length,
    };
  }, [data]);

  return (
    <Shell>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Services</h1>
          <p className="text-sm text-zinc-500">{data ? `Scanned ${timeAgo(data.scanned_at)}` : "Scanning…"}</p>
        </div>
        <div className="flex gap-2">
          <Button onClick={() => setConfirmNginx(true)}>Reload nginx</Button>
          <Button variant="primary" onClick={rescan} disabled={scanning}>
            {scanning ? "Scanning…" : "Rescan"}
          </Button>
        </div>
      </div>

      {notice && <p className="mb-3 rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300">{notice}</p>}
      {error && <div className="mb-3"><ErrorText><span className="whitespace-pre-wrap">{error}</span></ErrorText></div>}

      <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center">
        <div className="flex gap-1 text-sm">
          {(["all", "domains", "nodomain", "issues"] as Filter[]).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`rounded-md px-2.5 py-1 ${filter === f ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900" : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800"}`}
            >
              {{ all: "All", domains: "With domain", nodomain: "No domain", issues: "Issues" }[f]} <span className="opacity-60">{counts[f]}</span>
            </button>
          ))}
        </div>
        <Input placeholder="Search name, domain, path…" value={query} onChange={(e) => setQuery(e.target.value)} className="sm:ml-auto sm:max-w-xs" />
      </div>

      <ul className="divide-y divide-zinc-200 overflow-hidden rounded-lg border border-zinc-200 bg-white dark:divide-zinc-800 dark:border-zinc-800 dark:bg-zinc-900">
        {services.map((s) => <ServiceRow key={s.id} s={s} />)}
        {data && services.length === 0 && <li className="px-4 py-8 text-center text-sm text-zinc-500">No service matches.</li>}
        {!data && !error && <li className="px-4 py-8 text-center text-sm text-zinc-500">Loading…</li>}
      </ul>

      {data && data.warnings.length > 0 && (
        <details className="mt-4 text-sm text-zinc-500">
          <summary className="cursor-pointer">{data.warnings.length} scan warning(s)</summary>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {data.warnings.map((w) => <li key={w}>{w}</li>)}
          </ul>
        </details>
      )}

      <ConfirmDialog
        open={confirmNginx}
        title="Reload nginx?"
        message="Mas runs `nginx -t` first and only reloads if the configuration is valid."
        confirmLabel="Reload"
        onConfirm={reloadNginx}
        onCancel={() => setConfirmNginx(false)}
      />
    </Shell>
  );
}
