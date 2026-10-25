"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { DomainPicker, type DomainChoice } from "@/components/DomainPicker";
import { Shell } from "@/components/Shell";
import { MaintenanceCard } from "@/components/MaintenanceCard";
import { StackCard } from "@/components/StackCard";
import { TrafficStats } from "@/components/TrafficStats";
import {
  Badge, Button, Card, ConfirmDialog, CopyCode, ErrorText, Input, Label, Monogram, Notice, ReleaseBadge, Spinner,
  StatusDot, Terminal, Textarea,
} from "@/components/ui";
import { api, ApiError, SERVICE_TYPES, timeAgo, type App, type Release } from "@/lib/api";
import { ADVICE } from "@/lib/advice";
import { msg, tr, useT } from "@/lib/i18n";

const RUNNING = new Set(["queued", "building"]);
const BALANCED_TYPES = new Set(["nextjs", "node", "python"]);
const TRIGGERS: Record<string, string> = {
  push: msg("push"), manual: msg("manual"), rollback: msg("rollback"), create: msg("create"), cms: msg("CMS edit"),
  failover: msg("server down"), cluster: msg("cluster"),
};

interface ClusterServer { id: number; name: string; url: string; address: string | null; error: string | null }

interface ResourceSettings { memory_mb?: number; sleep_after_min?: number; autoscale?: { min: number; max: number } }
interface Advice { id: string; level: "info" | "warning"; params: Record<string, string | number>; set: ResourceSettings | null }
interface ResourcesInfo {
  resources: ResourceSettings; asleep: boolean; instances: number; last_request_at: number | null;
  target_rpm_per_instance: number; memory_now_mb: number | null; advice: Advice[];
}


function ResourcesCard({ app }: { app: App }) {
  const t = useT();
  const [info, setInfo] = useState<ResourcesInfo | null>(null);
  const [memory, setMemory] = useState("");
  const [sleep, setSleep] = useState("");
  const [auto, setAuto] = useState(false);
  const [low, setLow] = useState(1);
  const [high, setHigh] = useState(4);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const fill = (r: ResourcesInfo) => {
    setInfo(r);
    setMemory(r.resources.memory_mb ? String(r.resources.memory_mb) : "");
    setSleep(r.resources.sleep_after_min ? String(r.resources.sleep_after_min) : "");
    setAuto(!!r.resources.autoscale);
    setLow(r.resources.autoscale?.min ?? 1);
    setHigh(r.resources.autoscale?.max ?? Math.max(2, r.instances));
  };
  const load = useCallback(() => {
    api<ResourcesInfo>(`/apps/${encodeURIComponent(app.id)}/resources`).then(fill).catch((e) => setError((e as ApiError).detail));
  }, [app.id]);
  useEffect(load, [load]);

  async function save(value: ResourceSettings) {
    setBusy(true);
    setError("");
    setDone("");
    try {
      await api(`/apps/${encodeURIComponent(app.id)}/resources`, { method: "PUT", body: value });
      setDone(t("Applied: the instances restart one by one."));
      setTimeout(load, 3000);
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }
  const current = (): ResourceSettings => ({
    memory_mb: Number(memory) || undefined, sleep_after_min: Number(sleep) || undefined,
    autoscale: auto ? { min: low, max: high } : undefined,
  });

  if (!info) return error ? <Card title={t("Resources")}><ErrorText>{error}</ErrorText></Card> : null;
  return (
    <Card title={t("Resources")} actions={info.asleep ? <Badge tone="amber">{t("asleep")}</Badge>
      : info.memory_now_mb != null ? <Badge tone="emerald">{info.memory_now_mb} MB</Badge> : null}>
      <div className="space-y-4 text-sm">
        <p className="text-xs text-zinc-500">
          {info.asleep ? t("Stopped since nobody visits it: the next visit starts it (a few seconds).")
            : t("{n} instance(s) running.", { n: info.instances })}{" "}
          {info.last_request_at ? t("Last request {when}.", { when: timeAgo(new Date(info.last_request_at * 1000).toISOString()) }) : ""}
        </p>
        {info.advice.length > 0 && (
          <div className="space-y-2">
            {info.advice.map((a) => (
              <div key={a.id} className={`flex items-start justify-between gap-3 rounded-lg border px-3 py-2 ${a.level === "warning" ? "border-amber-300 bg-amber-50 dark:border-amber-500/30 dark:bg-amber-500/10" : "border-zinc-200 dark:border-white/[0.08]"}`}>
                <span className="text-xs">{t(ADVICE[a.id] ?? a.id, a.params)}</span>
                {a.set && <Button disabled={busy} onClick={() => save({ ...current(), ...a.set })}>{t("Apply")}</Button>}
              </div>
            ))}
          </div>
        )}
        <Label title={t("Memory limit (MB)")} hint={t("Above 90 % the app is slowed down, at the limit it restarts. Node apps get a matching heap size. Empty: no limit.")}>
          <Input type="number" min={64} max={65536} value={memory} onChange={(e) => setMemory(e.target.value)} placeholder="512" />
        </Label>
        <Label title={t("Sleep after (minutes without a visit)")} hint={t("The app is stopped and uses nothing; the next visitor sees a short “starting” page. Empty: always on.")}>
          <Input type="number" min={5} max={10080} value={sleep} onChange={(e) => setSleep(e.target.value)} placeholder="30" />
        </Label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
          <span>{t("Instances follow the traffic")}</span>
        </label>
        {auto && (
          <div className="space-y-2">
            <div className="grid grid-cols-2 gap-3">
              <Label title={t("Minimum")}><Input type="number" min={1} max={16} value={low} onChange={(e) => setLow(Math.max(1, Math.min(16, Number(e.target.value) || 1)))} /></Label>
              <Label title={t("Maximum")}><Input type="number" min={1} max={16} value={high} onChange={(e) => setHigh(Math.max(1, Math.min(16, Number(e.target.value) || 1)))} /></Label>
            </div>
            <p className="text-xs text-zinc-500">{t("One more instance per {rpm} requests a minute or when the CPU stays above 70 %, one less after 10 quiet minutes. No rebuild.", { rpm: info.target_rpm_per_instance })}</p>
          </div>
        )}
        <ErrorText>{error}</ErrorText>
        {done && <p className="text-xs text-emerald-600">{done}</p>}
        <Button variant="primary" disabled={busy} onClick={() => save(current())}>{busy ? <Spinner /> : null}{t("Save")}</Button>
      </div>
    </Card>
  );
}

function LoadBalancingCard({ app, onSaved }: { app: App; onSaved: (releaseId: number) => void }) {
  const t = useT();
  const [instances, setInstances] = useState(app.instances);
  const [backends, setBackends] = useState(app.backends.join("\n"));
  const [servers, setServers] = useState<ClusterServer[] | null>(null);
  const [chosen, setChosen] = useState<number[]>(app.cluster ?? []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    api<{ servers: ClusterServer[] }>("/cluster").then((r) => setServers(r.servers)).catch(() => setServers([]));
  }, []);
  const list = backends.split(/\s+/).filter(Boolean);
  const clusterChanged = [...chosen].sort().join() !== [...(app.cluster ?? [])].sort().join();
  const scalingChanged = instances !== app.instances || list.join() !== app.backends.join();
  const changed = clusterChanged || scalingChanged;
  const total = instances + list.length + chosen.length;

  async function save() {
    setBusy(true);
    setError("");
    try {
      let releaseId: number | null = null;
      if (clusterChanged) {
        const r = await api<{ release_id: number | null }>(`/apps/${encodeURIComponent(app.id)}/cluster?deploy=${scalingChanged ? "false" : "true"}`,
          { method: "PUT", body: { servers: chosen } });
        releaseId = r.release_id;
      }
      if (scalingChanged) {
        releaseId = (await api<{ release_id: number }>(`/apps/${encodeURIComponent(app.id)}/scaling`,
          { method: "PUT", body: { instances, backends: list } })).release_id;
      }
      if (releaseId) onSaved(releaseId);
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title={t("Load balancing")} actions={total > 1 ? <Badge tone="emerald">{t("{n} backends", { n: total })}</Badge> : <Badge>{t("single")}</Badge>}>
      <div className="space-y-4 text-sm">
        <Label title={t("Instances on this server")} hint={t("Ports {ports}. One per CPU core is a good maximum; redeploys restart them one by one.", { ports: app.instance_ports.join(", ") })}>
          <Input type="number" min={1} max={16} value={instances} onChange={(e) => setInstances(Math.max(1, Math.min(16, Number(e.target.value) || 1)))} />
        </Label>
        {servers && servers.length > 0 && (
          <div className="space-y-2">
            <div className="text-zinc-500">{t("Servers of the cluster")}</div>
            <p className="text-xs text-zinc-500">{t("Mas builds the same version on each server you tick, on every deploy, and adds the copies that answer to the load balancer.")} {t("If a server stays silent for 2 minutes, its copy is rebuilt on another available server.")}</p>
            {servers.map((s) => {
              const live = (app.cluster_backends ?? []).some((b) => s.address && b.startsWith(`${s.address}:`));
              return (
                <label key={s.id} className="flex items-center justify-between gap-3 rounded-lg border border-zinc-200 px-3 py-2 dark:border-white/[0.08]">
                  <span className="flex items-center gap-2">
                    <input type="checkbox" checked={chosen.includes(s.id)} onChange={(e) => setChosen(e.target.checked ? [...chosen, s.id] : chosen.filter((x) => x !== s.id))} />
                    <span className="font-medium">{s.name}</span>
                    <span className="mono text-xs text-zinc-500">{s.address}</span>
                  </span>
                  {s.error ? <Badge tone="red">{t("unreachable")}</Badge> : app.cluster?.includes(s.id) ? (live ? <Badge tone="emerald">{t("copy live")}</Badge> : <Badge tone="amber">{t("no copy")}</Badge>) : null}
                </label>
              );
            })}
          </div>
        )}
        <Label title={t("Other servers (optional)")} hint={t("One host:port per line, for copies of this app you run elsewhere (private network). nginx skips a server that stops answering.")}>
          <Textarea rows={3} value={backends} onChange={(e) => setBackends(e.target.value)} placeholder="10.0.0.12:3000" />
        </Label>
        {total > 1 && (
          <p className="text-xs text-zinc-500">{t("Requests are spread over every instance: keep sessions, uploads and caches in a shared place (database, Supabase storage), not in memory or on local disk.")}</p>
        )}
        <ErrorText>{error}</ErrorText>
        <Button variant="primary" disabled={!changed || busy} onClick={save}>{busy ? <Spinner /> : null}{t("Apply and redeploy")}</Button>
      </div>
    </Card>
  );
}

function colorize(line: string) {
  if (/^\d\d:\d\d:\d\d ==> /.test(line)) return "text-sky-300 font-medium";
  if (/error|failed|FAILED/.test(line)) return "text-rose-300";
  if (/warning/i.test(line)) return "text-amber-300";
  if (/ \$ /.test(line)) return "text-zinc-500";
  if (/is live|health: ok|✓/.test(line)) return "text-emerald-300";
  return "text-zinc-300";
}

function BuildLog({ appId, release, onEnd }: { appId: string; release: Release; onEnd: () => void }) {
  const t = useT();
  const [lines, setLines] = useState<string[]>([]);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setLines([]);
    const source = new EventSource(`/api/apps/${encodeURIComponent(appId)}/releases/${release.id}/stream`);
    source.onmessage = (e) => setLines((prev) => [...prev, e.data]);
    source.addEventListener("end", () => {
      source.close();
      onEnd();
    });
    source.onerror = () => source.close();
    return () => source.close();
  }, [appId, release.id, onEnd]);

  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [lines]);

  return (
    <Terminal title={`build #${release.id} · ${(release.sha ?? "").slice(0, 7)}`}>
      <div ref={box} className="mono max-h-[32rem] min-h-[14rem] overflow-auto p-4 text-[12px] leading-[1.7]">
        {lines.length === 0 && <span className="text-zinc-600">{t("Waiting for output…")}</span>}
        {lines.map((line, i) => (
          <div key={i} className={`whitespace-pre-wrap break-all ${colorize(line)}`}>{line || " "}</div>
        ))}
        {RUNNING.has(release.status) && <div className="mt-1 h-4 w-2 animate-pulse bg-zinc-400" />}
      </div>
    </Terminal>
  );
}

function DomainCard({ app, onLinked }: { app: App; onLinked: (releaseId: number) => void }) {
  const t = useT();
  const [editing, setEditing] = useState(!app.domain);
  const [choice, setChoice] = useState<DomainChoice | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const onChange = useCallback((c: DomainChoice | null) => setChoice(c), []);

  async function link() {
    if (!choice) return;
    setBusy(true);
    setError("");
    try {
      const r = await api<{ release_id: number }>(`/apps/${encodeURIComponent(app.id)}/domain`, {
        method: "POST",
        body: choice.zone ? { zone: choice.zone, subdomain: choice.subdomain, overwrite: choice.overwrite } : { domain: choice.domain },
      });
      setEditing(false);
      onLinked(r.release_id);
    } catch (e) {
      setError((e as ApiError).detail.replace(/^points-elsewhere: |^cname: /, ""));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title={t("Domain")}>
      {!editing && app.domain ? (
        <div className="flex items-center justify-between gap-2">
          <a href={`https://${app.domain}`} target="_blank" rel="noreferrer" className="flex min-w-0 items-center gap-2 text-sm font-medium hover:underline">
            <span className="h-2 w-2 shrink-0 rounded-full bg-emerald-400 shadow-[0_0_10px_2px_rgba(52,211,153,0.5)]" />
            <span className="truncate">{app.domain}</span>
          </a>
          <Button variant="ghost" onClick={() => setEditing(true)}>{t("Change")}</Button>
        </div>
      ) : (
        <div className="space-y-4">
          {!app.domain && <p className="text-sm text-zinc-500">{t("No address yet. Pick one: Mas creates the DNS record, the nginx site and the HTTPS certificate.")}</p>}
          <DomainPicker suggestion={app.id} onChange={onChange} />
          <ErrorText>{error}</ErrorText>
          <div className="flex gap-2">
            <Button variant="glow" onClick={link} disabled={!choice || busy}>{busy ? <Spinner /> : null}{t("Link domain")}</Button>
            {app.domain && <Button variant="ghost" onClick={() => setEditing(false)}>{t("Cancel")}</Button>}
          </div>
        </div>
      )}
    </Card>
  );
}

interface DbInfo {
  instance: string | null;
  connected: "local" | "external" | null;
  variables: string[];
  found: { uses_supabase: boolean; evidence: string[]; folder: string | null; seed: boolean };
  scan_error: string | null;
  migrations: { version: string; name: string; file: string; applied: boolean | null }[];
  seed_pending: boolean;
  queued: boolean;
  shared_with: string[];
}

/** Copies the public schema of a dev database (supabase.com) here and connects the app. */
function ImportDevDatabase({ appId, onDone }: { appId: string; onDone: (releaseId: number | null) => void }) {
  const t = useT();
  const [url, setUrl] = useState("");
  const [withData, setWithData] = useState(true);
  const [replace, setReplace] = useState(false);
  const [clash, setClash] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  async function run() {
    setBusy(true);
    setError("");
    setDone("");
    try {
      const r = await api<{ tables: string[]; connected: { release_id: number | null } | null }>(
        `/apps/${encodeURIComponent(appId)}/database/import`, { method: "POST", body: { url, with_data: withData, replace } });
      setUrl("");
      setDone(t("{n} table(s) imported.", { n: r.tables.length }));
      onDone(r.connected?.release_id ?? null);
    } catch (e) {
      const detail = (e as ApiError).detail;
      setClash(detail.includes("already exist"));
      setError(detail);
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="rounded-lg border border-zinc-200/80 p-3 dark:border-white/[0.06]">
      <summary className="cursor-pointer text-sm font-medium">{t("Import my dev database")}</summary>
      <div className="mt-3 space-y-3 text-sm">
        <p className="text-xs text-zinc-500">{t("Copies the tables of a Supabase project (supabase.com) here and connects this app. In the project: Connect → Session pooler, copy the connection string with its password. It is used once and not kept.")}</p>
        <Input type="password" autoComplete="off" placeholder="postgresql://postgres.xxxx:password@aws-0-eu-west-3.pooler.supabase.com:5432/postgres"
          value={url} onChange={(e) => setUrl(e.target.value)} />
        <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={withData} onChange={(e) => setWithData(e.target.checked)} /> {t("Copy the rows too (otherwise only the structure)")}</label>
        {clash && <label className="flex items-center gap-2 text-xs text-rose-500"><input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} /> {t("Replace the tables that already exist here (their rows are lost)")}</label>}
        <ErrorText>{error}</ErrorText>
        <Notice>{done}</Notice>
        <Button onClick={run} disabled={busy || !url.trim()}>{busy ? <Spinner /> : null}{t("Import and connect")}</Button>
      </div>
    </details>
  );
}

function DatabaseCard({ app, onLinked }: { app: App; onLinked: (releaseId: number) => void }) {
  const t = useT();
  const [info, setInfo] = useState<DbInfo | null>(null);
  const [seed, setSeed] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    api<DbInfo>(`/apps/${encodeURIComponent(app.id)}/database`).then(setInfo).catch((e: ApiError) => setError(e.detail));
  }, [app.id]);
  useEffect(load, [load]);

  async function connect() {
    setBusy(true);
    setError("");
    try {
      const r = await api<{ release_id: number }>(`/apps/${encodeURIComponent(app.id)}/supabase`, { method: "POST", body: { seed } });
      onLinked(r.release_id);
      load();
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  if (!info) return error ? <Card title={t("Database")}><ErrorText>{error}</ErrorText></Card> : null;
  const pending = info.migrations.filter((m) => m.applied === false).length;
  const uses = info.found.uses_supabase;
  const badge = info.connected === "local" ? <Badge tone="emerald">{t("connected")}</Badge>
    : info.connected === "external" ? <Badge tone="sky">{t("external Supabase")}</Badge>
    : uses ? <Badge tone="amber">{t("not connected")}</Badge> : null;

  return (
    <Card title={t("Database")} actions={badge}>
      <div className="space-y-3 text-sm">
        {uses && !info.connected && (
          <p className="text-zinc-500">{t("This app uses Supabase")} <span className="text-xs">({info.found.evidence.join(", ")})</span></p>
        )}

        {info.connected === "external" && (
          <p className="text-zinc-500">{t("Connected to another Supabase project through its environment variables: Mas leaves it alone.")}</p>
        )}

        {info.connected === "local" && (
          <>
            <p className="text-zinc-500">{t("Connected to the Supabase of this server.")} <span className="mono text-xs">{info.variables.join(", ")}</span></p>
            {info.migrations.length > 0 && (
              <div className="space-y-1">
                <div className="text-xs font-medium text-zinc-500">{t("Migrations ({folder})", { folder: `${info.found.folder}/migrations` })}</div>
                <ul className="space-y-0.5">
                  {info.migrations.map((m) => (
                    <li key={m.file} className="flex justify-between gap-2 text-xs">
                      <span className="mono truncate">{m.file}</span>
                      <span className={m.applied ? "text-emerald-500" : "text-amber-500"}>{m.applied === null ? "?" : m.applied ? t("applied") : t("at the next deploy")}</span>
                    </li>
                  ))}
                </ul>
                {pending > 0 && <p className="text-xs text-zinc-500">{t("New migrations run at every deploy, before the build. If one fails, the site stays on its current version.")}</p>}
              </div>
            )}
            {info.shared_with.length > 0 && <p className="text-xs text-zinc-500">{t("Same database as: {apps}.", { apps: info.shared_with.join(", ") })}</p>}
          </>
        )}

        {!info.connected && info.instance === "running" && (
          <div className="space-y-3">
            {!uses && <p className="text-zinc-500">{t("Give this app a database: Mas adds the URL and keys to its environment, then redeploys.")}</p>}
            {info.migrations.length > 0 && <p className="text-xs text-zinc-500">{t("Its {n} migration(s) will create the tables before the build.", { n: info.migrations.length })}</p>}
            {info.found.seed && <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={seed} onChange={(e) => setSeed(e.target.checked)} /> {t("Load supabase/seed.sql (sample data) once")}</label>}
            <ErrorText>{error}</ErrorText>
            <Button variant="primary" onClick={connect} disabled={busy}>{busy ? <Spinner /> : null}{t("Connect the database")}</Button>
          </div>
        )}

        {!info.connected && info.instance === "installing" && (
          <p className="flex items-center gap-2 text-zinc-500"><Spinner /> {info.queued ? t("Supabase is being installed: this app will be connected automatically.") : t("Supabase is being installed…")}</p>
        )}

        {!info.connected && (info.instance === null || info.instance === "failed" || info.instance === "stopped") && (
          uses ? (
            <div className="space-y-2">
              <p className="text-zinc-500">{info.instance === "stopped" ? t("The Supabase of this server is stopped.") : t("Mas can create the database on this server and connect this app as soon as it is ready.")}</p>
              <Link href={info.instance === "stopped" ? "/databases/" : `/databases/?connect=${encodeURIComponent(app.id)}`}
                className="inline-flex h-8 items-center rounded-lg bg-indigo-500 px-3 text-[13px] font-medium text-white hover:bg-indigo-400">
                {info.instance === "stopped" ? t("Open Databases") : t("Create the database and connect")} →
              </Link>
            </div>
          ) : (
            <p className="text-zinc-500">{t("Need a database?")} <Link href={`/databases/?connect=${encodeURIComponent(app.id)}`} className="font-medium text-indigo-500 hover:underline">{t("Create one on this server")} →</Link></p>
          )
        )}

        {info.instance === "running" && info.connected !== "external" && (
          <ImportDevDatabase appId={app.id} onDone={(rid) => { if (rid) onLinked(rid); load(); }} />
        )}
        {info.scan_error && <p className="text-xs text-zinc-500">{t("Could not read the repository: {error}", { error: info.scan_error })}</p>}
      </div>
    </Card>
  );
}

interface CmsCollectionInfo {
  id: string;
  label: string;
  storage: "git" | "supabase";
  dir: string | null;
  table: string | null;
  fields: number;
  error: string | null;
  sql?: string;
  local?: boolean;
  url?: string | null;
}

function SupabaseTable({ appId, collection, onDone }: { appId: string; collection: CmsCollectionInfo; onDone: () => void }) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function create() {
    setBusy(true);
    setError("");
    try {
      await api(`/apps/${encodeURIComponent(appId)}/cms/collections/${encodeURIComponent(collection.id)}/table`, { method: "POST" });
      onDone();
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2 rounded-lg border border-zinc-200/80 p-3 text-xs dark:border-white/[0.06]">
      <div className="flex flex-wrap items-center gap-2">
        {collection.error ? <Badge tone="amber">{t("to set up")}</Badge> : <Badge tone="emerald">{t("ready")}</Badge>}
        <span className="text-zinc-500">{collection.url ?? "Supabase"}</span>
      </div>
      {collection.error && <p className="text-zinc-500">{collection.error}</p>}
      {collection.local && (
        <Button onClick={create} disabled={busy}>{busy ? <Spinner /> : null}{collection.error ? t("Create the table") : t("Update the table")}</Button>
      )}
      <ErrorText>{error}</ErrorText>
      <details>
        <summary className="cursor-pointer text-zinc-500">
          {collection.local ? t("SQL run by Mas") : t("Run this SQL in the SQL editor of your Supabase project (again after adding fields)")}
        </summary>
        <div className="mt-2"><CopyCode text={collection.sql ?? ""} /></div>
      </details>
    </div>
  );
}

interface CmsInfo {
  schema_file: string;
  ok: boolean;
  error: string | null;
  can_write: boolean | null;
  pages: { id: string; label: string; file: string; fields: number }[];
  collections: CmsCollectionInfo[];
  editors: { id: number; username: string; allowed: boolean }[];
}

const CMS_DOCS = "https://github.com/SyanCode/Mas/blob/main/docs/cms.md";

const CMS_EXAMPLE = `{
  "$schema": "https://raw.githubusercontent.com/SyanCode/Mas/main/schema/mas.cms.schema.json",
  "pages": [{
    "id": "home", "label": "Home page", "file": "content/home.json",
    "fields": [
      { "key": "title", "type": "text", "max": 80, "required": true },
      { "key": "intro", "type": "textarea", "max": 500 },
      { "key": "hero", "type": "image", "dir": "public/uploads", "url": "/uploads" }
    ]
  }],
  "collections": [{
    "id": "articles", "label": "Articles", "item_label": "Article",
    "dir": "content/articles",
    "fields": [
      { "key": "title", "type": "text", "max": 120 },
      { "key": "date", "type": "date", "required": true },
      { "key": "cover", "type": "image", "dir": "public/uploads", "url": "/uploads" },
      { "key": "excerpt", "type": "textarea", "max": 300 },
      { "key": "body", "type": "markdown" }
    ]
  }]
}`;

interface Candidate { path: string; size: number; suggested: boolean }

function SchemaGenerator({ appId, replacing, onDone }: { appId: string; replacing: boolean; onDone: () => void }) {
  const t = useT();
  const [files, setFiles] = useState<Candidate[] | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  const [notes, setNotes] = useState<string[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const base = `/apps/${encodeURIComponent(appId)}/cms`;

  useEffect(() => {
    api<{ files: Candidate[] }>(`${base}/candidates`).then((r) => {
      setFiles(r.files);
      setPicked(r.files.filter((f) => f.suggested).map((f) => f.path));
    }).catch((e: ApiError) => setError(e.detail));
  }, [base]);

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : (e as Error).message);
    } finally {
      setBusy("");
    }
  }

  const generate = () => run("generate", async () => {
    const r = await api<{ schema: unknown; notes: string[] }>(`${base}/generate`, { method: "POST", body: { files: picked } });
    setDraft(JSON.stringify(r.schema, null, 2));
    setNotes(r.notes);
  });
  const commit = () => run("commit", async () => {
    let schema: unknown;
    try {
      schema = JSON.parse(draft);
    } catch {
      throw new Error(tr("the schema is not valid JSON"));
    }
    await api(`${base}/schema`, { method: "PUT", body: { schema } });
    onDone();
  });

  if (!files) return error ? <ErrorText>{error}</ErrorText> : <Spinner />;
  return (
    <div className="space-y-3 rounded-xl border border-zinc-200/80 p-3 dark:border-white/[0.06]">
      {!draft ? (
        <>
          <div className="text-xs font-medium text-zinc-500">{t("Content files to make editable")}</div>
          {files.length === 0 && <p className="text-xs text-zinc-500">{t("No JSON content file found. Move the texts of your site into JSON files (for example content/home.json), push, then come back.")}</p>}
          <div className="max-h-56 space-y-1 overflow-auto">
            {files.map((f) => (
              <label key={f.path} className="flex items-center gap-2 text-xs">
                <input type="checkbox" checked={picked.includes(f.path)}
                  onChange={(e) => setPicked(e.target.checked ? [...picked, f.path] : picked.filter((p) => p !== f.path))} />
                <code className="truncate">{f.path}</code>
              </label>
            ))}
          </div>
          <Button variant="primary" disabled={!picked.length || !!busy} onClick={generate}>{busy === "generate" ? <Spinner /> : null}{t("Generate")}</Button>
        </>
      ) : (
        <>
          <div className="text-xs text-zinc-500">{t("Review the fields (labels, max, required…), then commit.")}{replacing && ` ${t("This replaces the current file.")}`}</div>
          {notes.length > 0 && (
            <ul className="space-y-0.5 text-xs text-amber-500">{notes.map((n) => <li key={n}>• {n}</li>)}</ul>
          )}
          <Textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={14} spellCheck={false} />
          <div className="flex gap-2">
            <Button variant="primary" disabled={!!busy} onClick={commit}>{busy === "commit" ? <Spinner /> : null}{t("Commit mas.cms.json")}</Button>
            <Button variant="ghost" onClick={() => setDraft("")}>{t("Back")}</Button>
          </div>
        </>
      )}
      <ErrorText>{error}</ErrorText>
    </div>
  );
}

function CmsCard({ appId }: { appId: string }) {
  const t = useT();
  const [info, setInfo] = useState<CmsInfo | null>(null);
  const [generating, setGenerating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    api<CmsInfo>(`/apps/${encodeURIComponent(appId)}/cms`).then(setInfo).catch((e: ApiError) => setError(e.detail));
  }, [appId]);
  useEffect(load, [load]);

  async function toggle(id: number, allowed: boolean) {
    if (!info) return;
    const ids = info.editors.filter((e) => (e.id === id ? allowed : e.allowed)).map((e) => e.id);
    setBusy(true);
    setError("");
    try {
      await api(`/apps/${encodeURIComponent(appId)}/cms/editors`, { method: "PUT", body: { user_ids: ids } });
      load();
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="CMS" actions={info?.ok ? <Badge tone="emerald">{t("{n} page(s)", { n: info.pages.length })}{info.collections.length ? ` · ${t("{n} collection(s)", { n: info.collections.length })}` : ""}</Badge> : <Badge>{t("off")}</Badge>}>
      {!info ? <ErrorText>{error}</ErrorText> : (
        <div className="space-y-4 text-sm">
          {info.ok ? (
            <>
              <ul className="space-y-1">
                {info.pages.map((p) => (
                  <li key={p.id} className="flex justify-between gap-2"><span>{p.label}</span><code className="truncate text-xs text-zinc-500">{p.file}</code></li>
                ))}
                {info.collections.map((c) => (
                  <li key={c.id} className="space-y-2">
                    <div className="flex justify-between gap-2">
                      <span>{c.label}</span>
                      <code className="truncate text-xs text-zinc-500">{c.storage === "supabase" ? `Supabase · ${c.table}` : `${c.dir}/*.json`}</code>
                    </div>
                    {c.storage === "supabase" && <SupabaseTable appId={appId} collection={c} onDone={load} />}
                  </li>
                ))}
              </ul>
              {info.can_write === false && (
                <p className="text-xs text-amber-500">{t("The GitHub App can only read this repository. On GitHub: Settings → Developer settings → GitHub Apps → your Mas app → Permissions → Contents: Read and write, then accept the new permissions on the installation.")}</p>
              )}
              <div className="flex flex-wrap items-center gap-3">
                <Link href={`/cms/edit/?app=${encodeURIComponent(appId)}`} className="inline-flex font-medium text-indigo-500 hover:underline">{t("Open the editor")} →</Link>
                {!generating && <button className="text-xs text-zinc-500 hover:underline" onClick={() => setGenerating(true)}>{t("Regenerate {file}", { file: info.schema_file })}</button>}
              </div>
              {generating && <SchemaGenerator appId={appId} replacing onDone={() => { setGenerating(false); load(); }} />}
            </>
          ) : (
            <div className="space-y-3">
              <p className="text-zinc-500">{t("Let your client edit the content: Mas reads the JSON content files of the repository and writes {file} for you. Your site reads those JSON files at build time.", { file: info.schema_file })}</p>
              {info.error && <p className="text-xs text-zinc-500">{info.error}</p>}
              <SchemaGenerator appId={appId} replacing={false} onDone={load} />
              <details className="text-xs text-zinc-500">
                <summary className="cursor-pointer">{t("Or write it by hand")}</summary>
                <pre className="mono mt-2 overflow-auto rounded-lg bg-black/40 p-3 text-[11px] leading-relaxed text-zinc-300">{CMS_EXAMPLE}</pre>
              </details>
              <a href={CMS_DOCS} target="_blank" rel="noreferrer" className="inline-block text-xs text-indigo-500 hover:underline">{t("mas.cms.json reference")} ↗</a>
            </div>
          )}
          <div className="space-y-1.5 border-t border-zinc-200/80 pt-3 dark:border-white/[0.06]">
            <div className="text-xs font-medium text-zinc-500">{t("Editors with access")}</div>
            {info.editors.map((e) => (
              <label key={e.id} className="flex items-center gap-2">
                <input type="checkbox" checked={e.allowed} disabled={busy} onChange={(ev) => toggle(e.id, ev.target.checked)} /> {e.username}
              </label>
            ))}
            {info.editors.length === 0 && <p className="text-xs text-zinc-500">{t("Create client accounts in")} <Link href="/settings/" className="text-indigo-500 hover:underline">{t("Settings → People")}</Link>.</p>}
          </div>
          <ErrorText>{error}</ErrorText>
        </div>
      )}
    </Card>
  );
}

function SettingsCard({ app, onSaved }: { app: App; onSaved: (a: App) => void }) {
  const t = useT();
  const [branch, setBranch] = useState(app.branch);
  const [buildCommand, setBuildCommand] = useState(app.build_command ?? "");
  const [startCommand, setStartCommand] = useState(app.start_command ?? "");
  const [type, setType] = useState<string>(app.type);
  const [rootDir, setRootDir] = useState(app.root_dir ?? "");
  const [autoDeploy, setAutoDeploy] = useState(app.auto_deploy);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  async function save() {
    setMsg("");
    setError("");
    const body: Record<string, unknown> = { branch, build_command: buildCommand, auto_deploy: autoDeploy };
    if (app.mode === "managed") {
      body.start_command = startCommand;
      body.root_dir = rootDir;
      if (type !== app.type) body.type = type;
    }
    try {
      onSaved(await api<App>(`/apps/${encodeURIComponent(app.id)}`, { method: "PATCH", body }));
      setMsg(t("Saved. Redeploy to apply."));
    } catch (e) {
      setError((e as ApiError).detail);
    }
  }

  return (
    <Card title={t("Build & deploy")}>
      <div className="space-y-4">
        <Label title={t("Production branch")}><Input value={branch} onChange={(e) => setBranch(e.target.value)} /></Label>
        <Label title={t("Build command")} hint={t("Empty: detected from the repository.")}><Input value={buildCommand} onChange={(e) => setBuildCommand(e.target.value)} placeholder="npm run build" className="mono text-xs" /></Label>
        {app.mode === "managed" && (
          <>
            <Label title={t("Type")} hint={t("A site whose build only writes pages (dist/, build/…) is static.")}>
              <select value={type} onChange={(e) => setType(e.target.value)} className="w-full rounded-md border border-zinc-300 bg-white px-2 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-950">
                {SERVICE_TYPES.filter((x) => x !== "unknown").map((x) => <option key={x}>{x}</option>)}
              </select>
            </Label>
            {(type === "static" || type === "php") && (
              <Label title={t("Folder to serve (optional)")}><Input value={rootDir} onChange={(e) => setRootDir(e.target.value)} placeholder="public, dist, out…" className="mono text-xs" /></Label>
            )}
            {(type === "node" || type === "nextjs" || type === "python") && (
              <Label title={t("Start command")}><Input value={startCommand} onChange={(e) => setStartCommand(e.target.value)} placeholder="next start / npm start" className="mono text-xs" /></Label>
            )}
          </>
        )}
        <label className="flex items-center justify-between gap-3 text-sm">
          <span>
            {t("Deploy on every push")}
            <span className="block text-xs text-zinc-500">{autoDeploy ? t("Off: the app is frozen, pushes are recorded and wait for you.") : t("Frozen: pushes are recorded, nothing builds until you deploy.")}</span>
          </span>
          <button
            type="button"
            onClick={() => setAutoDeploy(!autoDeploy)}
            className={`relative h-5 w-9 rounded-full transition ${autoDeploy ? "bg-gradient-to-r from-indigo-500 to-sky-500" : "bg-zinc-300 dark:bg-white/10"}`}
          >
            <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition ${autoDeploy ? "left-[18px]" : "left-0.5"}`} />
          </button>
        </label>
        <Notice>{msg}</Notice>
        <ErrorText>{error}</ErrorText>
        <Button variant="primary" onClick={save}>{t("Save")}</Button>
      </div>
    </Card>
  );
}

function EnvCard({ appId }: { appId: string }) {
  const t = useT();
  const [text, setText] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  async function reveal() {
    try {
      setText((await api<{ text: string }>(`/apps/${encodeURIComponent(appId)}/env`)).text);
    } catch (e) {
      setError((e as ApiError).detail);
    }
  }

  async function save() {
    setError("");
    try {
      const r = await api<{ count: number }>(`/apps/${encodeURIComponent(appId)}/env`, { method: "PUT", body: { text: text ?? "" } });
      setMsg(t("{n} variable(s) saved, encrypted. Redeploy to apply.", { n: r.count }));
    } catch (e) {
      setError((e as ApiError).detail);
    }
  }

  return (
    <Card title={t("Environment variables")} actions={<Badge tone="violet">{t("encrypted")}</Badge>}>
      {text === null ? (
        <Button onClick={reveal}>{t("Reveal and edit")}</Button>
      ) : (
        <div className="space-y-3">
          <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={7} placeholder="KEY=value" />
          <Button variant="primary" onClick={save}>{t("Save")}</Button>
        </div>
      )}
      <div className="mt-3 space-y-2"><Notice>{msg}</Notice><ErrorText>{error}</ErrorText></div>
    </Card>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-xs text-zinc-500">{label}</div>
      <div className="mt-1 truncate text-sm font-medium">{children}</div>
    </div>
  );
}

function AppView() {
  const t = useT();
  const params = useSearchParams();
  const id = params.get("id") ?? "";
  const [app, setApp] = useState<App | null>(null);
  const [selected, setSelected] = useState<number | null>(params.get("release") ? Number(params.get("release")) : null);
  const [error, setError] = useState("");
  const [confirmRollback, setConfirmRollback] = useState<Release | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [purge, setPurge] = useState(false);

  const load = useCallback(() => {
    api<App>(`/apps/${encodeURIComponent(id)}`).then(setApp).catch((e: ApiError) => setError(e.detail));
  }, [id]);
  useEffect(() => { if (id) load(); }, [id, load]);

  // Keep statuses fresh while something is building (also catches pushes from GitHub).
  const busy = (app?.releases ?? []).some((r) => RUNNING.has(r.status));
  useEffect(() => {
    const timer = setInterval(load, busy ? 3_000 : 15_000);
    return () => clearInterval(timer);
  }, [busy, load]);

  async function post(path: string, body?: unknown) {
    try {
      const r = await api<{ release_id: number }>(`/apps/${encodeURIComponent(id)}${path}`, { method: "POST", body });
      setSelected(r.release_id);
      load();
    } catch (e) {
      setError((e as ApiError).detail);
    }
  }

  async function remove() {
    setConfirmDelete(false);
    try {
      await api(`/apps/${encodeURIComponent(id)}?purge=${purge}`, { method: "DELETE" });
      window.location.href = "/apps/";
    } catch (e) {
      setError((e as ApiError).detail);
    }
  }

  if (error && !app) return <ErrorText>{error}</ErrorText>;
  if (!app) return <div className="glass shimmer h-48 rounded-2xl" />;

  const releases = app.releases ?? [];
  const shown = releases.find((r) => r.id === selected) ?? releases[0];
  const current = app.current;
  const building = releases.some((r) => RUNNING.has(r.status));

  return (
    <div className="space-y-6">
      <Link href="/apps/" className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-white">← {t("Apps")}</Link>

      <div className="flex flex-wrap items-center justify-between gap-4 animate-fade-up">
        <div className="flex min-w-0 items-center gap-4">
          <Monogram name={app.id} size="lg" />
          <div className="min-w-0">
            <h1 className="truncate text-[28px] font-semibold tracking-[-0.02em]">{app.id}</h1>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-zinc-500">
              {app.mode === "stack"
                ? <><span>{t(app.name)}</span><span>·</span><Badge tone="emerald">{t("service")}</Badge></>
                : <><a href={`https://github.com/${app.repo}`} target="_blank" rel="noreferrer" className="hover:text-zinc-900 dark:hover:text-white">{app.repo}</a>
                  <span>·</span>
                  <Badge tone="sky">{app.type}</Badge></>}
              {app.mode === "adopted" && <Badge tone="violet">{t("adopted")} · {app.path}</Badge>}
              {app.mode !== "stack" && !app.auto_deploy && <Badge tone="sky">❄ {t("frozen")}</Badge>}
              {app.project && <Link href={`/projects/view/?id=${encodeURIComponent(app.project)}`}><Badge tone="violet">◆ {t("project {name}", { name: app.project })}</Badge></Link>}
            </div>
          </div>
        </div>
        <div className="flex gap-2">
          {app.domain && (
            <a href={`https://${app.domain}`} target="_blank" rel="noreferrer" className="glass inline-flex h-8 items-center rounded-lg px-3 text-[13px] font-medium hover:bg-white/[0.08]">
              {t("Visit")} ↗
            </a>
          )}
          <Button variant="glow" onClick={() => post("/deploy")} disabled={building}>{building ? <Spinner /> : null}{building ? t("Deploying…") : t("Redeploy")}</Button>
          <Button variant="ghost" onClick={() => setConfirmDelete(true)}>{t("Remove")}</Button>
        </div>
      </div>

      <ErrorText>{error}</ErrorText>

      {app.pending && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-amber-400/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-800 dark:text-amber-200">
          <span className="text-lg">❄</span>
          <span className="min-w-0 flex-1">
            <span className="font-medium">{t("{n} commit(s) waiting", { n: app.pending.commits })}</span>
            {" · "}<code className="text-xs">{app.pending.sha.slice(0, 7)}</code> {app.pending.message}
            <span className="opacity-70"> · {app.pending.author} · {timeAgo(app.pending.at)}</span>
          </span>
          <Button onClick={() => post("/deploy")} disabled={building}>{t("Deploy them now")}</Button>
          <Button variant="ghost" onClick={async () => { await api("/apps/freeze", { method: "POST", body: { app_ids: [app.id], frozen: false } }); load(); }}>{t("Unfreeze")}</Button>
        </div>
      )}

      <section className="glass ring-gradient relative overflow-hidden rounded-2xl p-6 animate-fade-up">
        <div className="pointer-events-none absolute -right-24 -top-24 h-64 w-64 rounded-full bg-indigo-500/20 blur-3xl" />
        <div className="mb-5 flex items-center gap-2 text-xs font-medium uppercase tracking-[0.18em] text-zinc-500">
          <StatusDot status={current ? "live" : building ? "building" : "unknown"} size="sm" /> {t("Production")}
        </div>
        <div className="grid grid-cols-2 gap-6 md:grid-cols-5">
          <Field label={t("Status")}>{current ? <ReleaseBadge status="live" /> : building ? <ReleaseBadge status="building" /> : t("Not deployed")}</Field>
          <Field label={t("Domain")}>{app.domain ? <a href={`https://${app.domain}`} target="_blank" rel="noreferrer" className="hover:underline">{app.domain}</a> : <span className="text-zinc-500">—</span>}</Field>
          {app.mode === "stack" ? (
            <>
              <Field label={t("Service")}>{t(app.name)}</Field>
              <Field label={t("Open ports")}>{Object.values(app.stack?.ports ?? {}).join(", ") || "—"}</Field>
            </>
          ) : (
            <>
              <Field label={t("Branch")}><code className="text-xs">{app.branch}</code></Field>
              <Field label={t("Commit")}>{current ? <span title={current.message ?? ""}><code className="text-xs">{(current.sha ?? "").slice(0, 7)}</code> {current.message}</span> : "—"}</Field>
            </>
          )}
          <Field label={t("Deployed")}>{current ? `${timeAgo(current.created_at)}${current.actor ? ` ${t("by {who}", { who: current.actor })}` : ""}` : "—"}</Field>
        </div>
      </section>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          {shown ? (
            <div className="space-y-3 animate-fade-up">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-semibold">{t("Deployment #{id}", { id: shown.id })}</span>
                <ReleaseBadge status={shown.status} />
                <span className="text-zinc-500">{t(TRIGGERS[shown.trigger] ?? shown.trigger)}{shown.actor ? ` ${t("by {who}", { who: shown.actor })}` : ""} · {timeAgo(shown.created_at)}</span>
              </div>
              {shown.error && <ErrorText><span className="whitespace-pre-wrap">{shown.error}</span></ErrorText>}
              <BuildLog appId={app.id} release={shown} onEnd={load} />
            </div>
          ) : (
            <Card><p className="text-sm text-zinc-500">{t("No deployment yet. Click “Redeploy”.")}</p></Card>
          )}

          {app.domain && <TrafficStats url={`/apps/${encodeURIComponent(app.id)}/stats`} title={t("Traffic")} />}

          <Card title={t("Deployments")} padded={false}>
            <ol className="relative">
              {releases.map((r, i) => (
                <li key={r.id} className={`group relative flex items-center gap-4 px-5 py-3 transition hover:bg-zinc-50 dark:hover:bg-white/[0.03] ${r.id === shown?.id ? "bg-indigo-500/[0.06]" : ""}`}>
                  <div className="relative flex w-3 justify-center self-stretch">
                    {i < releases.length - 1 && <span className="absolute top-1/2 h-full w-px bg-zinc-200 dark:bg-white/[0.08]" />}
                    <span className="relative mt-[calc(50%-6px)]"><StatusDot status={r.status} size="sm" /></span>
                  </div>
                  <button onClick={() => setSelected(r.id)} className="flex min-w-0 flex-1 items-center gap-3 text-left">
                    <code className="text-xs text-zinc-500">{(r.sha ?? "").slice(0, 7) || "—"}</code>
                    <span className="truncate text-sm">{r.message || t(TRIGGERS[r.trigger] ?? r.trigger)}</span>
                  </button>
                  {r.id === app.current_release && <Badge tone="emerald">{t("current")}</Badge>}
                  <span className="hidden text-xs text-zinc-500 sm:block">{timeAgo(r.created_at)}</span>
                  {r.path && r.status === "superseded" && (
                    <Button className="!h-7 opacity-0 transition group-hover:opacity-100" onClick={() => setConfirmRollback(r)}>{t("Rollback")}</Button>
                  )}
                </li>
              ))}
              {releases.length === 0 && <li className="px-5 py-4 text-sm text-zinc-500">{t("No deployment yet.")}</li>}
            </ol>
          </Card>
        </div>

        <div className="space-y-6">
          {app.mode === "managed" && <DomainCard key={app.domain ?? ""} app={app} onLinked={(rid) => { setSelected(rid); load(); }} />}
          {app.domain && (app.mode === "managed" || app.mode === "stack") && <MaintenanceCard appId={app.id} />}
          {app.mode === "stack" && <StackCard appId={app.id} onDeployed={(rid) => { setSelected(rid); load(); }} />}
          {app.mode !== "stack" && <SettingsCard app={app} onSaved={(a) => setApp({ ...a, releases: app.releases })} />}
          {app.mode === "managed" && BALANCED_TYPES.has(app.type) && app.port && (
            <LoadBalancingCard key={`${app.instances}-${app.backends.join()}`} app={app} onSaved={(rid) => { setSelected(rid); load(); }} />
          )}
          {app.mode === "managed" && BALANCED_TYPES.has(app.type) && app.port && <ResourcesCard app={app} />}
          {app.mode === "managed" && <DatabaseCard app={app} onLinked={(rid) => { setSelected(rid); load(); }} />}
          {app.installation_id && <CmsCard appId={app.id} />}
          {app.mode === "managed" && <EnvCard appId={app.id} />}
        </div>
      </div>

      <ConfirmDialog
        open={confirmRollback !== null}
        title={t("Roll back to #{id}?", { id: confirmRollback?.id ?? "" })}
        message={t("Commit {sha} goes back to production instantly; the current release stays available.", { sha: (confirmRollback?.sha ?? "").slice(0, 7) })}
        confirmLabel={t("Roll back")}
        onConfirm={() => {
          const target = confirmRollback;
          setConfirmRollback(null);
          if (target) post("/rollback", { release_id: target.id });
        }}
        onCancel={() => setConfirmRollback(null)}
      />
      <ConfirmDialog
        open={confirmDelete}
        title={t("Remove {name}?", { name: app.id })}
        message={
          app.mode === "adopted" ? t("Mas stops deploying this service; the service itself is not touched.") : app.mode === "stack" ? (
            <span className="block space-y-2">
              <span className="block">{t("Its containers are stopped and removed, and its address goes offline.")}</span>
              <label className="flex items-center gap-2 text-rose-500"><input type="checkbox" checked={purge} onChange={(e) => setPurge(e.target.checked)} /> {t("Also delete its data (/srv/mas/stacks/{id})", { id: app.id })}</label>
            </span>
          ) : (
            <span className="block space-y-2">
              <span className="block">{t("The systemd unit and the nginx site are removed (backups are kept). The site goes offline.")}</span>
              <label className="flex items-center gap-2"><input type="checkbox" checked={purge} onChange={(e) => setPurge(e.target.checked)} /> {t("Also delete /srv/mas/apps/{id}", { id: app.id })}</label>
            </span>
          )
        }
        confirmLabel={t("Remove")}
        danger
        onConfirm={remove}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}

export default function AppViewPage() {
  return (
    <Shell>
      <Suspense fallback={<div className="glass shimmer h-48 rounded-2xl" />}>
        <AppView />
      </Suspense>
    </Shell>
  );
}
