"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { DomainPicker, type DomainChoice } from "@/components/DomainPicker";
import { Shell } from "@/components/Shell";
import {
  Badge, Button, Card, ConfirmDialog, CopyCode, ErrorText, Notice, PageHeader, Spinner, StatusDot, Terminal, Textarea,
} from "@/components/ui";
import { api, ApiError, timeAgo } from "@/lib/api";
import { msg, useT } from "@/lib/i18n";

interface Instance {
  status: "installing" | "running" | "stopped" | "failed";
  domain: string | null;
  ref: string | null;
  error: string | null;
  last_backup: string | null;
  created_at: string;
  ports: Record<string, number>;
  api_url: string | null;
  studio_access: string[];
  components: string[];
  estimated_ram_mb: number;
}
interface Component { id: string; label: string; help: string; ram_mb: number; default: boolean }
interface Preset { id: string; label: string; help: string; components: string[]; ram_mb: number }
interface Catalog { core_ram_mb: number; components: Component[]; presets: Preset[] }
interface Stats { containers: { name: string; mb: number }[]; total_mb: number }

// Component and preset names and descriptions come from the API in English.
const COMPONENT_TEXTS = [
  msg("Admin dashboard"), msg("browse tables, users and files in a web page (Studio, private)"),
  msg("Live updates"), msg("pages update by themselves when data changes: chat, notifications (Realtime)"),
  msg("File uploads"), msg("photos and documents sent by your users (Storage)"),
  msg("Image resizing"), msg("thumbnails made on the fly from uploaded photos (imgproxy)"),
  msg("Server functions"), msg("small pieces of server code run by Supabase (Edge Functions)"),
  msg("Logs and statistics"), msg("request history in the admin dashboard, the heaviest part (Logflare)"),
  msg("Many connections"), msg("for apps opening lots of database connections, such as serverless functions (Supavisor)"),
  msg("Essential"), msg("a database, user accounts and the API: enough for most sites and apps"),
  msg("Recommended"), msg("adds file uploads, live updates and the admin dashboard"),
  msg("Complete"), msg("everything Supabase offers, including logs and server functions"),
];

const sameSet = (a: string[], b: string[]) => [...a].sort().join() === [...b].sort().join();

/** The preset that fits the free memory best (Recommended when it fits). */
function suggested(catalog: Catalog, freeMb: number | null): string[] {
  const fits = catalog.presets.filter((p) => freeMb === null || p.ram_mb <= freeMb);
  const pick = fits.find((p) => p.id === "recommended") ?? fits.find((p) => p.id === "essential") ?? catalog.presets[0];
  return pick ? pick.components : [];
}

function ramOf(catalog: Catalog, value: string[]) {
  return catalog.core_ram_mb + catalog.components.filter((c) => value.includes(c.id)).reduce((n, c) => n + c.ram_mb, 0);
}
interface Requirements { docker: string | null; compose: string | null; memory_available_mb: number | null; memory_recommended_mb: number }
interface Credentials {
  api_url: string; anon_key: string; service_role_key: string; database_url: string;
  studio: { tunnel: string; url: string; web: string | null; username: string; password: string };
}
interface Backup { name: string; size: number; created_at: string }

function InstallLog({ onEnd }: { onEnd: () => void }) {
  const t = useT();
  const [lines, setLines] = useState<string[]>([]);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const source = new EventSource("/api/supabase/log/stream");
    source.onmessage = (e) => setLines((prev) => [...prev, e.data]);
    source.addEventListener("end", () => { source.close(); onEnd(); });
    source.onerror = () => source.close();
    return () => source.close();
  }, [onEnd]);
  useEffect(() => { if (box.current) box.current.scrollTop = box.current.scrollHeight; }, [lines]);
  return (
    <Terminal title="supabase install">
      <div ref={box} className="mono max-h-[26rem] min-h-[12rem] overflow-auto p-4 text-[12px] leading-[1.7] text-zinc-300">
        {lines.map((l, i) => <div key={i} className={/==>/.test(l) ? "font-medium text-sky-300" : /error|FAILED/.test(l) ? "text-rose-300" : /warning/.test(l) ? "text-amber-300" : ""}>{l || " "}</div>)}
        {lines.length === 0 && <span className="text-zinc-600">{t("Starting…")}</span>}
      </div>
    </Terminal>
  );
}

function PresetPicker({ catalog, value, onChange, freeMb }: { catalog: Catalog; value: string[]; onChange: (v: string[]) => void; freeMb: number | null }) {
  const t = useT();
  const preset = catalog.presets.find((p) => sameSet(p.components, value));
  const [custom, setCustom] = useState(false);
  return (
    <div className="space-y-2">
      <div className="text-sm font-medium">{t("What will your apps need?")}</div>
      <div className="grid gap-2 sm:grid-cols-3">
        {catalog.presets.map((p) => {
          const active = preset?.id === p.id;
          return (
            <button key={p.id} type="button" onClick={() => onChange(p.components)}
              className={`rounded-xl border p-3 text-left text-sm transition ${active ? "border-indigo-400 bg-indigo-500/10" : "border-zinc-200 hover:bg-zinc-100 dark:border-white/10 dark:hover:bg-white/5"}`}>
              <span className="flex items-center justify-between gap-2">
                <span className="font-medium">{t(p.label)}</span>
                {freeMb !== null && p.ram_mb > freeMb && <Badge tone="amber">{t("tight")}</Badge>}
              </span>
              <span className="mt-1 block text-xs text-zinc-500">{t(p.help)}</span>
              <span className="mono mt-2 block text-xs text-zinc-500">≈ {p.ram_mb} MB RAM</span>
            </button>
          );
        })}
      </div>
      {preset && !custom
        ? <button type="button" className="text-xs text-zinc-500 hover:underline" onClick={() => setCustom(true)}>{t("Customize")}</button>
        : <ComponentPicker catalog={catalog} value={value} onChange={onChange} />}
    </div>
  );
}

function ComponentPicker({ catalog, value, onChange }: { catalog: Catalog; value: string[]; onChange: (v: string[]) => void }) {
  const t = useT();
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-sm">
        <span className="font-medium">{t("Parts")}</span>
        <span className="mono text-xs text-zinc-500">≈ {ramOf(catalog, value)} MB RAM</span>
      </div>
      <div className="flex items-center justify-between rounded-lg border border-white/5 px-3 py-2 text-sm text-zinc-500">
        <span>{t("Database, user accounts and API")} <span className="text-xs">({t("always on")})</span></span>
        <span className="mono text-xs">{catalog.core_ram_mb} MB</span>
      </div>
      {catalog.components.map((c) => (
        <label key={c.id} className="flex cursor-pointer items-center gap-3 rounded-lg border border-white/5 px-3 py-2 text-sm hover:bg-white/5">
          <input type="checkbox" checked={value.includes(c.id)}
            onChange={(e) => onChange(e.target.checked ? [...value, c.id] : value.filter((v) => v !== c.id))} />
          <span className="flex-1">{t(c.label)} <span className="text-xs text-zinc-500">— {t(c.help)}</span></span>
          <span className="mono text-xs text-zinc-500">+{c.ram_mb} MB</span>
        </label>
      ))}
    </div>
  );
}

function Secret({ label, value }: { label: string; value: string }) {
  const t = useT();
  const [shown, setShown] = useState(false);
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs text-zinc-500">
        <span>{label}</span>
        <button onClick={() => setShown(!shown)} className="hover:underline">{shown ? t("hide") : t("reveal")}</button>
      </div>
      <CopyCode text={shown ? value : "•".repeat(32)} />
    </div>
  );
}

function Databases() {
  const t = useT();
  const [inst, setInst] = useState<Instance | null | undefined>(undefined);
  const [reqs, setReqs] = useState<Requirements | null>(null);
  const [withDomain, setWithDomain] = useState(true);
  const [choice, setChoice] = useState<DomainChoice | null>(null);
  const [creds, setCreds] = useState<Credentials | null>(null);
  const [backups, setBackups] = useState<Backup[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [restoreBackup, setRestoreBackup] = useState<string | null>(null);
  const [purge, setPurge] = useState(false);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [components, setComponents] = useState<string[] | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [connectApp, setConnectApp] = useState<string | null>(null); // ?connect=<app>: connected once installed
  const [access, setAccess] = useState("");  // Studio: allowed addresses, one per line
  const [network, setNetwork] = useState<{ local: string[]; you: string } | null>(null);
  const onDomain = useCallback((c: DomainChoice | null) => setChoice(c), []);
  useEffect(() => { setConnectApp(new URLSearchParams(window.location.search).get("connect")); }, []);

  const load = useCallback(() => {
    api<{ instance: Instance | null; requirements: Requirements; catalog: Catalog; local_networks: string[]; your_ip: string }>("/supabase").then((r) => {
      setInst(r.instance);
      setAccess((r.instance?.studio_access ?? []).join("\n"));
      setNetwork({ local: r.local_networks, you: r.your_ip });
      setReqs(r.requirements);
      setCatalog(r.catalog);
      setComponents((prev) => r.instance ? r.instance.components : prev ?? suggested(r.catalog, r.requirements.memory_available_mb));
      if (r.instance && r.instance.status !== "installing") api<Backup[]>("/supabase/backups").then(setBackups).catch(() => {});
      if (r.instance?.status === "running") api<Stats>("/supabase/stats").then(setStats).catch(() => {});
      else setStats(null);
    }).catch((e: ApiError) => setError(e.detail));
  }, []);
  useEffect(() => { load(); }, [load]);

  async function run(label: string, fn: () => Promise<unknown>, done?: string) {
    setBusy(label);
    setError("");
    setMsg("");
    try {
      await fn();
      if (done) setMsg(done);
      load();
    } catch (e) {
      setError((e as ApiError).detail.replace(/^points-elsewhere: |^cname: /, ""));
    } finally {
      setBusy("");
    }
  }

  async function deploy() {
    await run("deploy", async () => {
      let domain: string | null = null;
      if (withDomain && choice) {
        if (choice.zone) await api("/dns/records", { method: "POST", body: { zone: choice.zone, subdomain: choice.subdomain, overwrite: choice.overwrite } });
        domain = choice.domain;
      }
      await api("/supabase", { method: "POST", body: { domain, components, connect: connectApp ? [connectApp] : [] } });
    });
  }

  const estimate = catalog && components ? ramOf(catalog, components) : reqs?.memory_recommended_mb ?? 0;
  const memOk = !reqs?.memory_available_mb || reqs.memory_available_mb >= estimate;
  const changed = !!inst && !!components && [...components].sort().join() !== [...inst.components].sort().join();
  const studioUp = !!stats?.containers.some((c) => c.name.includes("studio"));
  const ready = !!reqs?.docker && !!reqs?.compose;
  const accessList = access.split(/[\s,]+/).filter(Boolean);
  const accessChanged = !!inst && accessList.join() !== inst.studio_access.join();

  return (
    <>
      <PageHeader eyebrow={t("Databases")} title={<>{t("Self-hosted")} <span className="text-gradient">Supabase</span></>}
        subtitle={t("Postgres, Auth, Storage, Realtime and the REST API on your own server, connected to your apps in one click.")} />
      <div className="mb-4 space-y-3"><Notice>{msg}</Notice><ErrorText>{error}</ErrorText></div>

      {inst === undefined && <div className="glass shimmer h-48 rounded-2xl" />}

      {inst === null && reqs && (
        <section className="glass ring-gradient relative overflow-hidden rounded-2xl p-6">
          <div className="pointer-events-none absolute -right-20 -top-20 h-64 w-64 rounded-full bg-emerald-500/20 blur-3xl" />
          <div className="grid gap-8 lg:grid-cols-2">
            <div className="space-y-4">
              <h2 className="text-lg font-semibold">{t("Deploy Supabase")}</h2>
              {connectApp && <Notice>{t("{app} will be connected to it automatically once it is installed.", { app: connectApp })}</Notice>}
              <ul className="space-y-2 text-sm text-zinc-500">
                <li>• {t("Official docker stack, every port bound to 127.0.0.1")}</li>
                <li>• {t("The API is published (HTTPS); Studio only answers the local network and the addresses you allow")}</li>
                <li>• {t("Secrets generated and stored encrypted; daily backups kept 7 days")}</li>
              </ul>
              <div className="flex flex-wrap gap-2">
                <Badge tone={reqs.docker ? "emerald" : "red"}>Docker {reqs.docker ?? t("missing")}</Badge>
                <Badge tone={reqs.compose ? "emerald" : "red"}>Compose {reqs.compose ?? t("missing")}</Badge>
                {reqs.memory_available_mb !== null && (
                  <Badge tone={memOk ? "emerald" : "amber"}>{t("{n} GB RAM free", { n: (reqs.memory_available_mb / 1024).toFixed(1) })}</Badge>
                )}
              </div>
              {!memOk && <p className="text-xs text-amber-500">{t("These components need about {n} MB of free memory; your other services may slow down.", { n: estimate })}</p>}
              {catalog && components && <PresetPicker catalog={catalog} value={components} onChange={setComponents} freeMb={reqs.memory_available_mb} />}
            </div>
            <div className="space-y-4">
              <label className="flex items-center gap-2 text-sm font-medium">
                <input type="checkbox" checked={withDomain} onChange={(e) => setWithDomain(e.target.checked)} /> {t("Publish the API on a domain")}
              </label>
              {withDomain && <DomainPicker suggestion="db" onChange={onDomain} />}
              <Button variant="glow" className="!h-10 px-6" disabled={!ready || busy === "deploy" || (withDomain && !choice)} onClick={deploy}>
                {busy === "deploy" ? <Spinner /> : null}{t("Deploy Supabase")}
              </Button>
            </div>
          </div>
        </section>
      )}

      {inst && (inst.status === "installing" || inst.status === "failed") && (
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-sm">
            <StatusDot status={inst.status === "installing" ? "building" : "failed"} />
            <span className="font-medium">{inst.status === "installing" ? t("Installing Supabase…") : t("Installation failed")}</span>
          </div>
          {inst.error && <ErrorText>{inst.error}</ErrorText>}
          <InstallLog onEnd={load} />
          {inst.status === "failed" && (
            <div className="flex gap-2">
              <Button variant="ghost" onClick={() => setConfirmRemove(true)}>{t("Remove and start over")}</Button>
            </div>
          )}
        </div>
      )}

      {inst && (inst.status === "running" || inst.status === "stopped") && (
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="space-y-6 lg:col-span-2">
            <section className="glass ring-gradient rounded-2xl p-6">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <StatusDot status={inst.status === "running" ? "running" : "stopped"} size="lg" />
                  <div>
                    <div className="font-semibold">Supabase</div>
                    <div className="text-xs text-zinc-500">{inst.api_url} · {t("since {when}", { when: timeAgo(inst.created_at) })}</div>
                  </div>
                </div>
                <div className="flex gap-2">
                  {inst.status === "running"
                    ? <><Button onClick={() => run("restart", () => api("/supabase/actions/restart", { method: "POST" }), t("Restarted."))} disabled={!!busy}>{t("Restart")}</Button>
                        <Button onClick={() => run("stop", () => api("/supabase/actions/stop", { method: "POST" }))} disabled={!!busy}>{t("Stop")}</Button></>
                    : <Button variant="primary" onClick={() => run("start", () => api("/supabase/actions/start", { method: "POST" }))} disabled={!!busy}>{t("Start")}</Button>}
                  <Button variant="ghost" onClick={() => setConfirmRemove(true)}>{t("Remove")}</Button>
                </div>
              </div>
            </section>

            <Card title={t("Connection")} actions={<Badge tone="violet">{t("secret")}</Badge>}>
              {!creds ? (
                <Button onClick={() => api<Credentials>("/supabase/credentials").then(setCreds).catch((e: ApiError) => setError(e.detail))}>{t("Show credentials")}</Button>
              ) : (
                <div className="space-y-4">
                  <div className="space-y-1"><div className="text-xs text-zinc-500">API URL</div><CopyCode text={creds.api_url} /></div>
                  <Secret label={t("anon key (public, for the browser)")} value={creds.anon_key} />
                  <Secret label={t("service_role key (server only, bypasses RLS)")} value={creds.service_role_key} />
                  <Secret label={t("DATABASE_URL (from this server)")} value={creds.database_url} />
                  <p className="text-xs text-zinc-500">{t("Or open an app and click Connect Supabase: Mas adds these variables and redeploys.")}</p>
                </div>
              )}
            </Card>

            {catalog && components && (
              <Card title={t("Features")} actions={<span className="mono text-xs text-zinc-500">≈ {inst.estimated_ram_mb} MB</span>}>
                <div className="space-y-4">
                  <PresetPicker catalog={catalog} value={components} onChange={setComponents} freeMb={null} />
                  <div className="flex flex-wrap items-center gap-2">
                    <Button variant="primary" disabled={!changed || !!busy}
                      onClick={() => run("components", () => api("/supabase/components", { method: "PUT", body: { components } }), t("Components updated."))}>
                      {busy === "components" ? <Spinner /> : null}{t("Apply")}
                    </Button>
                    {changed && <Button variant="ghost" onClick={() => setComponents(inst.components)}>{t("Cancel")}</Button>}
                    <span className="text-xs text-zinc-500">{t("Disabled services are stopped and removed; data volumes are kept.")}</span>
                  </div>
                </div>
              </Card>
            )}

            {inst.domain && (
              <Card title={t("Studio on {domain}", { domain: inst.domain })}>
                <div className="space-y-3 text-sm">
                  <p className="text-zinc-500">
                    {t("The admin interface opens at")}{" "}
                    <a href={`https://${inst.domain}/`} target="_blank" rel="noreferrer" className="text-sky-500 hover:underline">https://{inst.domain}/</a>{" "}
                    {t("from the local network and from the addresses below; everybody else gets a 403. The API stays public.")}
                  </p>
                  {network && <p className="text-xs text-zinc-500">{t("Always allowed (local network): {networks}", { networks: network.local.join(", ") })}</p>}
                  <Textarea rows={4} value={access} onChange={(e) => setAccess(e.target.value)} placeholder={"203.0.113.7\n198.51.100.0/24"}
                    aria-label={t("Allowed IP addresses or networks, one per line")} />
                  <div className="flex flex-wrap items-center gap-2">
                    <Button variant="primary" disabled={!accessChanged || !!busy}
                      onClick={() => run("access", () => api("/supabase/studio-access", { method: "PUT", body: { networks: accessList } }), t("Studio access updated."))}>
                      {busy === "access" ? <Spinner /> : null}{t("Save")}
                    </Button>
                    {network && !accessList.includes(network.you) && (
                      <Button variant="ghost" onClick={() => setAccess([...accessList, network.you].join("\n"))}>{t("Add my IP ({ip})", { ip: network.you })}</Button>
                    )}
                    <span className="text-xs text-zinc-500">{t("One IP or network per line, such as 203.0.113.7 or 198.51.100.0/24.")}</span>
                  </div>
                </div>
              </Card>
            )}

            {creds && (
              <Card title={inst.domain ? t("Studio sign-in") : t("Studio (private)")}>
                <div className="space-y-3 text-sm">
                  <p className="text-zinc-500">{inst.domain ? t("From anywhere else, through an SSH tunnel:") : t("Studio is not exposed to the internet. From your computer:")}</p>
                  <CopyCode text={creds.studio.tunnel} />
                  <p className="text-zinc-500">{t("then open {url} and sign in:", { url: creds.studio.url })}</p>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="space-y-1"><div className="text-xs text-zinc-500">{t("Username")}</div><CopyCode text={creds.studio.username} /></div>
                    <Secret label={t("Password")} value={creds.studio.password} />
                  </div>
                </div>
              </Card>
            )}
          </div>

          <div className="space-y-6">
          <Card title={t("Memory")} actions={stats ? <span className="mono text-xs text-zinc-500">{stats.total_mb.toFixed(0)} MB</span> : null}>
            {!stats || stats.containers.length === 0 ? (
              <p className="text-sm text-zinc-500">{inst.status === "running" ? t("Loading…") : t("Stack stopped.")}</p>
            ) : (
              <ul className="space-y-1.5 text-sm">
                {stats.containers.map((c) => (
                  <li key={c.name} className="flex justify-between gap-2"><span className="mono truncate text-xs">{c.name.replace(/^supabase-/, "")}</span><span className="mono shrink-0 text-xs text-zinc-500">{c.mb.toFixed(0)} MB</span></li>
                ))}
              </ul>
            )}
            {inst.status === "running" && inst.components.includes("studio") && (
              <div className="mt-4 flex items-center justify-between gap-2 border-t border-white/5 pt-4 text-xs text-zinc-500">
                <span>{studioUp ? t("Studio running: start it only when you need it.") : t("Studio stopped: start it only when you need it.")}</span>
                <Button disabled={!!busy} onClick={() => run("studio", () => api(`/supabase/studio/${studioUp ? "stop" : "start"}`, { method: "POST" }), studioUp ? t("Studio stopped.") : t("Studio started."))}>
                  {busy === "studio" ? <Spinner /> : null}{studioUp ? t("Stop Studio") : t("Start Studio")}
                </Button>
              </div>
            )}
          </Card>

          <Card title={t("Backups")} actions={<Button onClick={() => run("backup", () => api("/supabase/backups", { method: "POST" }), t("Backup created."))} disabled={!!busy || inst.status !== "running"}>{busy === "backup" ? <Spinner /> : null}{t("Backup now")}</Button>}>
            <p className="mb-3 text-xs text-zinc-500">{t("Daily pg_dump, last 7 kept in /var/lib/mas/backups/supabase.")}</p>
            <ul className="space-y-2 text-sm">
              {backups.map((b) => (
                <li key={b.name} className="flex items-center justify-between gap-2">
                  <span className="mono truncate text-xs">{b.name}</span>
                  <span className="flex shrink-0 items-center gap-2 text-xs text-zinc-500">
                    {(b.size / 1048576).toFixed(1)} MB · {timeAgo(b.created_at)}
                    <Button disabled={!!busy || inst.status !== "running"} onClick={() => setRestoreBackup(b.name)}>{t("Restore")}</Button>
                  </span>
                </li>
              ))}
              {backups.length === 0 && <li className="text-zinc-500">{t("No backup yet.")}</li>}
            </ul>
          </Card>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={confirmRemove}
        title={t("Remove Supabase?")}
        message={<span className="block space-y-2"><span className="block">{t("The stack is stopped and its nginx site removed. Backups are kept.")}</span>
          <label className="flex items-center gap-2 text-rose-500"><input type="checkbox" checked={purge} onChange={(e) => setPurge(e.target.checked)} /> {t("Also delete the database volumes (all data)")}</label></span>}
        confirmLabel={t("Remove")}
        danger
        onConfirm={() => { setConfirmRemove(false); run("remove", () => api(`/supabase?purge=${purge}`, { method: "DELETE" }), t("Supabase removed.")); }}
        onCancel={() => setConfirmRemove(false)}
      />
      <ConfirmDialog
        open={restoreBackup !== null}
        title={t("Restore database backup?")}
        message={<span className="block space-y-2"><span className="block">{t("This replaces database objects with the selected backup. Mas first saves the current database as a safety backup.")}</span><code className="mono block break-all text-xs">{restoreBackup}</code></span>}
        confirmLabel={t("Restore backup")}
        danger
        onConfirm={() => {
          const name = restoreBackup;
          setRestoreBackup(null);
          if (name) run("restore", () => api<{ safety_backup: string }>(`/supabase/backups/${encodeURIComponent(name)}/restore`, { method: "POST" })
            .then((r) => setMsg(t("Database restored. Safety backup: {name}", { name: r.safety_backup }))));
        }}
        onCancel={() => setRestoreBackup(null)}
      />
    </>
  );
}

export default function DatabasesPage() {
  return (
    <Shell>
      <Databases />
    </Shell>
  );
}
