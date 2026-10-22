"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { Badge, Button, Card, ConfirmDialog, ErrorText, PageHeader, Stat } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { msg, useT } from "@/lib/i18n";
import { ADVICE } from "@/lib/advice";

interface Settings { memory_mb?: number; sleep_after_min?: number; autoscale?: { min: number; max: number } }
interface Advice { id: string; level: "info" | "warning"; params: Record<string, string | number>; set: Settings | null }
interface Row {
  key: string; kind: string; name: string; memory_mb: number; cpu_percent: number | null; parts: string[];
  app_id?: string; service_id?: string | null; limit_mb?: number | null; sleep_after_min?: number | null;
  autoscale?: { min: number; max: number } | null; asleep?: boolean; instances?: number; resources?: Settings; advice?: Advice[];
}
interface Usage {
  server: {
    cpus: number; cpu_percent: number | null; load: number[];
    memory: { total_mb: number | null; used_mb: number | null; available_mb: number | null };
    swap: { total_mb: number | null; used_mb: number | null }; disk: { total_gb: number; used_gb: number } | null;
  };
  rows: Row[]; cgroups: boolean; kernel_and_cache_mb: number | null;
}

const KINDS: Record<string, string> = {
  app: msg("Mas app"), stack: msg("service"), supabase: "Supabase", containers: msg("containers"), service: msg("system service"),
  mas: "Mas", user: msg("user sessions"),
};
const COLORS = ["bg-indigo-500", "bg-sky-500", "bg-fuchsia-500", "bg-emerald-500", "bg-amber-500", "bg-rose-500", "bg-violet-500", "bg-teal-500"];
const mb = (n: number | null | undefined) => (n == null ? "—" : n >= 1024 ? `${(n / 1024).toFixed(1)} GB` : `${Math.round(n)} MB`);

function Bar({ value, max, className = "bg-indigo-500" }: { value: number; max: number; className?: string }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-white/[0.06]">
      <div className={`h-full rounded-full ${className}`} style={{ width: `${Math.min(100, max ? (value / max) * 100 : 0)}%` }} />
    </div>
  );
}

function Resources() {
  const t = useT();
  const [data, setData] = useState<Usage | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [done, setDone] = useState("");
  const [confirmAll, setConfirmAll] = useState(false);
  const load = useCallback(() => {
    api<Usage>("/usage").then((d) => { setData(d); setError(""); }).catch((e) => setError((e as ApiError).detail));
  }, []);
  useEffect(() => {
    load();
    const id = setInterval(load, 10_000);
    return () => clearInterval(id);
  }, [load]);

  async function apply(row: Row, set: Settings) {
    await api(`/apps/${encodeURIComponent(row.app_id!)}/resources`, { method: "PUT", body: { ...row.resources, ...set } });
  }
  async function applyOne(row: Row, a: Advice) {
    setBusy(`${row.key}:${a.id}`);
    setError("");
    try {
      await apply(row, a.set!);
      setDone(t("Applied to {name}: its instances restart one by one.", { name: row.name }));
      setTimeout(load, 3000);
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy("");
    }
  }
  async function applyAll() {
    setConfirmAll(false);
    setBusy("all");
    setError("");
    let n = 0;
    try {
      for (const row of data?.rows ?? []) {
        const merged = (row.advice ?? []).filter((a) => a.set).reduce<Settings>((acc, a) => ({ ...acc, ...a.set }), {});
        if (Object.keys(merged).length) {
          await apply(row, merged);
          n++;
        }
      }
      setDone(t("Advice applied to {n} app(s).", { n }));
      setTimeout(load, 3000);
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy("");
    }
  }

  const s = data?.server;
  const total = s?.memory.total_mb ?? 0;
  const rows = data?.rows ?? [];
  const top = rows.filter((r) => r.kind !== "user").slice(0, COLORS.length);
  const advice = rows.flatMap((r) => (r.advice ?? []).filter((a) => a.set).map((a) => ({ row: r, a })));
  const asleep = rows.filter((r) => r.asleep).length;
  const limited = rows.filter((r) => r.app_id && r.limit_mb).length;
  const apps = rows.filter((r) => r.app_id && r.kind === "app").length;

  return (
    <>
      <PageHeader eyebrow={t("Server")} title={t("Resources")}
        subtitle={t("What uses the memory and the CPU of this server, everything at once. Refreshed every 10 seconds.")}
        actions={advice.length > 0 ? <Button variant="primary" disabled={busy !== ""} onClick={() => setConfirmAll(true)}>{t("Apply all advice ({n})", { n: advice.length })}</Button> : undefined} />

      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="CPU" value={s?.cpu_percent != null ? `${s.cpu_percent} %` : "…"}
          hint={s ? t("load {load} / {n} cores", { load: s.load[0].toFixed(2), n: s.cpus }) : undefined} tone={s && (s.cpu_percent ?? 0) > 80 ? "red" : "sky"} />
        <Stat label={t("Memory")} value={s?.memory.used_mb != null ? mb(s.memory.used_mb) : "…"}
          hint={s ? t("{pct} % of {total} · {free} free", { pct: Math.round(((s.memory.used_mb ?? 0) / (total || 1)) * 100), total: mb(total), free: mb(s.memory.available_mb) }) : undefined}
          tone={s && (s.memory.used_mb ?? 0) / (total || 1) > 0.85 ? "red" : "violet"} />
        <Stat label="Swap" value={s?.swap.total_mb ? mb(s.swap.used_mb) : t("none")} hint={s?.swap.total_mb ? t("of {total}", { total: mb(s.swap.total_mb) }) : undefined}
          tone={s && (s.swap.used_mb ?? 0) > 256 ? "amber" : "zinc"} />
        <Stat label={t("Apps")} value={`${limited}/${apps}`} hint={t("with a memory limit · {n} asleep", { n: asleep })} tone="emerald" />
      </div>

      <ErrorText>{error}</ErrorText>
      {done && <p className="mb-4 text-sm text-emerald-600">{done}</p>}

      {data && (
        <Card title={t("Memory by service")} className="mb-6">
          <div className="flex h-4 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-white/[0.06]">
            {top.map((r, i) => (
              <div key={r.key} className={COLORS[i]} style={{ width: `${(r.memory_mb / (total || 1)) * 100}%` }} title={`${r.name} · ${mb(r.memory_mb)}`} />
            ))}
          </div>
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-500">
            {top.map((r, i) => (
              <span key={r.key} className="inline-flex items-center gap-1.5"><span className={`h-2 w-2 rounded-full ${COLORS[i]}`} />{r.name} · {mb(r.memory_mb)}</span>
            ))}
            {data.kernel_and_cache_mb != null && <span>{t("system and cache: {mb}", { mb: mb(data.kernel_and_cache_mb) })}</span>}
          </div>
          {!data.cgroups && <p className="mt-3 text-xs text-amber-600">{t("This server does not use cgroup v2: only the apps of Mas are measured.")}</p>}
        </Card>
      )}

      <Card padded={false}>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-200 text-xs text-zinc-500 dark:border-white/[0.06]">
                <th className="px-4 py-3 font-medium">{t("Service")}</th>
                <th className="px-4 py-3 font-medium">{t("Memory")}</th>
                <th className="px-4 py-3 font-medium">CPU</th>
                <th className="px-4 py-3 font-medium">{t("Limit")}</th>
                <th className="px-4 py-3 font-medium">{t("State")}</th>
                <th className="px-4 py-3 font-medium">{t("Advice")}</th>
              </tr>
            </thead>
            <tbody>
              {!data && <tr><td colSpan={6} className="px-4 py-6"><div className="glass shimmer h-24 rounded-xl" /></td></tr>}
              {rows.map((r) => {
                const href = r.app_id ? `/apps/view/?id=${encodeURIComponent(r.app_id)}` : r.service_id ? `/service/?id=${encodeURIComponent(r.service_id)}` : null;
                const near = r.limit_mb && r.memory_mb / Math.max(1, r.instances ?? 1) > r.limit_mb * 0.9;
                return (
                  <tr key={r.key} className="border-b border-zinc-100 align-top last:border-0 dark:border-white/[0.04]">
                    <td className="px-4 py-3">
                      {href ? <Link href={href} className="font-medium hover:underline">{r.name}</Link> : <span className="font-medium">{r.name}</span>}
                      <div className="text-xs text-zinc-500">{t(KINDS[r.kind] ?? r.kind)}{r.parts.length > 1 ? ` · ${t("{n} parts", { n: r.parts.length })}` : ""}</div>
                    </td>
                    <td className="w-48 px-4 py-3">
                      <div className="mb-1 tabular-nums">{mb(r.memory_mb)} <span className="text-xs text-zinc-500">{total ? `${((r.memory_mb / total) * 100).toFixed(1)} %` : ""}</span></div>
                      <Bar value={r.memory_mb} max={total} className={near ? "bg-amber-500" : "bg-indigo-500"} />
                    </td>
                    <td className="px-4 py-3 tabular-nums">{r.cpu_percent != null ? `${r.cpu_percent} %` : "—"}</td>
                    <td className="px-4 py-3 tabular-nums">{r.limit_mb ? `${mb(r.limit_mb)}${r.instances && r.instances > 1 ? ` ×${r.instances}` : ""}` : <span className="text-zinc-500">{t("none")}</span>}</td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-1">
                        {r.asleep && <Badge tone="amber">{t("asleep")}</Badge>}
                        {r.instances != null && r.instances > 0 && <Badge>{t("{n} instance(s)", { n: r.instances })}</Badge>}
                        {r.autoscale && <Badge tone="sky">{r.autoscale.min}–{r.autoscale.max}</Badge>}
                        {r.sleep_after_min && <Badge tone="violet">{t("sleeps after {n} min", { n: r.sleep_after_min })}</Badge>}
                      </div>
                    </td>
                    <td className="max-w-sm px-4 py-3">
                      <div className="space-y-2">
                        {(r.advice ?? []).map((a) => (
                          <div key={a.id} className="flex items-start gap-2 text-xs">
                            <span className={a.level === "warning" ? "text-amber-600 dark:text-amber-400" : "text-zinc-600 dark:text-zinc-400"}>{t(ADVICE[a.id] ?? a.id, a.params)}</span>
                            {a.set && <Button className="shrink-0" disabled={busy !== ""} onClick={() => applyOne(r, a)}>{busy === `${r.key}:${a.id}` ? "…" : t("Apply")}</Button>}
                          </div>
                        ))}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      <ConfirmDialog open={confirmAll} title={t("Apply all advice?")}
        message={t("Each app gets the suggested limit, sleep or autoscaling. Their instances restart one by one; nothing is rebuilt.")}
        confirmLabel={t("Apply")} onConfirm={applyAll} onCancel={() => setConfirmAll(false)} />
    </>
  );
}

export default function ResourcesPage() {
  return (
    <Shell>
      <Resources />
    </Shell>
  );
}
