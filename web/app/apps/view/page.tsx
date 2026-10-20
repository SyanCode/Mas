"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { DomainPicker, type DomainChoice } from "@/components/DomainPicker";
import { Shell } from "@/components/Shell";
import {
  Badge, Button, Card, ConfirmDialog, ErrorText, Input, Label, Monogram, Notice, ReleaseBadge, Spinner, StatusDot,
  Terminal, Textarea,
} from "@/components/ui";
import { api, ApiError, timeAgo, type App, type Release } from "@/lib/api";

const RUNNING = new Set(["queued", "building"]);

function colorize(line: string) {
  if (/^\d\d:\d\d:\d\d ==> /.test(line)) return "text-sky-300 font-medium";
  if (/error|failed|FAILED/.test(line)) return "text-rose-300";
  if (/warning/i.test(line)) return "text-amber-300";
  if (/ \$ /.test(line)) return "text-zinc-500";
  if (/is live|health: ok|✓/.test(line)) return "text-emerald-300";
  return "text-zinc-300";
}

function BuildLog({ appId, release, onEnd }: { appId: string; release: Release; onEnd: () => void }) {
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
        {lines.length === 0 && <span className="text-zinc-600">Waiting for output…</span>}
        {lines.map((line, i) => (
          <div key={i} className={`whitespace-pre-wrap break-all ${colorize(line)}`}>{line || " "}</div>
        ))}
        {RUNNING.has(release.status) && <div className="mt-1 h-4 w-2 animate-pulse bg-zinc-400" />}
      </div>
    </Terminal>
  );
}

function DomainCard({ app, onLinked }: { app: App; onLinked: (releaseId: number) => void }) {
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
    <Card title="Domain">
      {!editing && app.domain ? (
        <div className="flex items-center justify-between gap-2">
          <a href={`https://${app.domain}`} target="_blank" rel="noreferrer" className="flex min-w-0 items-center gap-2 text-sm font-medium hover:underline">
            <span className="h-2 w-2 shrink-0 rounded-full bg-emerald-400 shadow-[0_0_10px_2px_rgba(52,211,153,0.5)]" />
            <span className="truncate">{app.domain}</span>
          </a>
          <Button variant="ghost" onClick={() => setEditing(true)}>Change</Button>
        </div>
      ) : (
        <div className="space-y-4">
          {!app.domain && <p className="text-sm text-zinc-500">No address yet. Pick one: Mas creates the DNS record, the nginx site and the HTTPS certificate.</p>}
          <DomainPicker suggestion={app.id} onChange={onChange} />
          <ErrorText>{error}</ErrorText>
          <div className="flex gap-2">
            <Button variant="glow" onClick={link} disabled={!choice || busy}>{busy ? <Spinner /> : null}Link domain</Button>
            {app.domain && <Button variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>}
          </div>
        </div>
      )}
    </Card>
  );
}

function SupabaseCard({ app, onLinked }: { app: App; onLinked: (releaseId: number) => void }) {
  const [status, setStatus] = useState<string | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    api<{ instance: { status: string } | null }>("/supabase").then((r) => setStatus(r.instance?.status ?? null)).catch(() => setStatus(null));
  }, []);

  async function connect() {
    setBusy(true);
    setError("");
    try {
      onLinked((await api<{ release_id: number }>(`/apps/${encodeURIComponent(app.id)}/supabase`, { method: "POST" })).release_id);
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  if (status === undefined) return null;
  return (
    <Card title="Supabase" actions={status === "running" ? <Badge tone="emerald">available</Badge> : null}>
      {status === "running" ? (
        <div className="space-y-3 text-sm">
          <p className="text-zinc-500">Adds <code>SUPABASE_URL</code>, the anon and service_role keys and <code>DATABASE_URL</code> to this app, then redeploys.</p>
          <ErrorText>{error}</ErrorText>
          <Button variant="primary" onClick={connect} disabled={busy}>{busy ? <Spinner /> : null}Connect Supabase</Button>
        </div>
      ) : (
        <p className="text-sm text-zinc-500">Need a database? <Link href="/databases/" className="font-medium text-indigo-500 hover:underline">Deploy Supabase on this server →</Link></p>
      )}
    </Card>
  );
}

function SettingsCard({ app, onSaved }: { app: App; onSaved: (a: App) => void }) {
  const [branch, setBranch] = useState(app.branch);
  const [buildCommand, setBuildCommand] = useState(app.build_command ?? "");
  const [startCommand, setStartCommand] = useState(app.start_command ?? "");
  const [autoDeploy, setAutoDeploy] = useState(app.auto_deploy);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  async function save() {
    setMsg("");
    setError("");
    const body: Record<string, unknown> = { branch, build_command: buildCommand, auto_deploy: autoDeploy };
    if (app.mode === "managed") body.start_command = startCommand;
    try {
      onSaved(await api<App>(`/apps/${encodeURIComponent(app.id)}`, { method: "PATCH", body }));
      setMsg("Saved. Redeploy to apply.");
    } catch (e) {
      setError((e as ApiError).detail);
    }
  }

  return (
    <Card title="Build & deploy">
      <div className="space-y-4">
        <Label title="Production branch"><Input value={branch} onChange={(e) => setBranch(e.target.value)} /></Label>
        <Label title="Build command" hint="Empty: detected from the repository."><Input value={buildCommand} onChange={(e) => setBuildCommand(e.target.value)} placeholder="npm run build" className="mono text-xs" /></Label>
        {app.mode === "managed" && (
          <Label title="Start command"><Input value={startCommand} onChange={(e) => setStartCommand(e.target.value)} placeholder="next start / npm start" className="mono text-xs" /></Label>
        )}
        <label className="flex items-center justify-between gap-3 text-sm">
          <span>Deploy on every push</span>
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
        <Button variant="primary" onClick={save}>Save</Button>
      </div>
    </Card>
  );
}

function EnvCard({ appId }: { appId: string }) {
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
      setMsg(`${r.count} variable(s) saved, encrypted. Redeploy to apply.`);
    } catch (e) {
      setError((e as ApiError).detail);
    }
  }

  return (
    <Card title="Environment variables" actions={<Badge tone="violet">encrypted</Badge>}>
      {text === null ? (
        <Button onClick={reveal}>Reveal and edit</Button>
      ) : (
        <div className="space-y-3">
          <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={7} placeholder="KEY=value" />
          <Button variant="primary" onClick={save}>Save</Button>
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
    const t = setInterval(load, busy ? 3_000 : 15_000);
    return () => clearInterval(t);
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
      <Link href="/apps/" className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-white">← Apps</Link>

      <div className="flex flex-wrap items-center justify-between gap-4 animate-fade-up">
        <div className="flex min-w-0 items-center gap-4">
          <Monogram name={app.id} size="lg" />
          <div className="min-w-0">
            <h1 className="truncate text-[28px] font-semibold tracking-[-0.02em]">{app.id}</h1>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-zinc-500">
              <a href={`https://github.com/${app.repo}`} target="_blank" rel="noreferrer" className="hover:text-zinc-900 dark:hover:text-white">{app.repo}</a>
              <span>·</span>
              <Badge tone="sky">{app.type}</Badge>
              {app.mode === "adopted" && <Badge tone="violet">adopted · {app.path}</Badge>}
            </div>
          </div>
        </div>
        <div className="flex gap-2">
          {app.domain && (
            <a href={`https://${app.domain}`} target="_blank" rel="noreferrer" className="glass inline-flex h-8 items-center rounded-lg px-3 text-[13px] font-medium hover:bg-white/[0.08]">
              Visit ↗
            </a>
          )}
          <Button variant="glow" onClick={() => post("/deploy")} disabled={building}>{building ? <Spinner /> : null}{building ? "Deploying…" : "Redeploy"}</Button>
          <Button variant="ghost" onClick={() => setConfirmDelete(true)}>Remove</Button>
        </div>
      </div>

      <ErrorText>{error}</ErrorText>

      <section className="glass ring-gradient relative overflow-hidden rounded-2xl p-6 animate-fade-up">
        <div className="pointer-events-none absolute -right-24 -top-24 h-64 w-64 rounded-full bg-indigo-500/20 blur-3xl" />
        <div className="mb-5 flex items-center gap-2 text-xs font-medium uppercase tracking-[0.18em] text-zinc-500">
          <StatusDot status={current ? "live" : building ? "building" : "unknown"} size="sm" /> Production
        </div>
        <div className="grid grid-cols-2 gap-6 md:grid-cols-5">
          <Field label="Status">{current ? <ReleaseBadge status="live" /> : building ? <ReleaseBadge status="building" /> : "Not deployed"}</Field>
          <Field label="Domain">{app.domain ? <a href={`https://${app.domain}`} target="_blank" rel="noreferrer" className="hover:underline">{app.domain}</a> : <span className="text-zinc-500">—</span>}</Field>
          <Field label="Branch"><code className="text-xs">{app.branch}</code></Field>
          <Field label="Commit">{current ? <span title={current.message ?? ""}><code className="text-xs">{(current.sha ?? "").slice(0, 7)}</code> {current.message}</span> : "—"}</Field>
          <Field label="Deployed">{current ? `${timeAgo(current.created_at)}${current.actor ? ` by ${current.actor}` : ""}` : "—"}</Field>
        </div>
      </section>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          {shown ? (
            <div className="space-y-3 animate-fade-up">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-semibold">Deployment #{shown.id}</span>
                <ReleaseBadge status={shown.status} />
                <span className="text-zinc-500">{shown.trigger}{shown.actor ? ` by ${shown.actor}` : ""} · {timeAgo(shown.created_at)}</span>
              </div>
              {shown.error && <ErrorText><span className="whitespace-pre-wrap">{shown.error}</span></ErrorText>}
              <BuildLog appId={app.id} release={shown} onEnd={load} />
            </div>
          ) : (
            <Card><p className="text-sm text-zinc-500">No deployment yet. Click “Redeploy”.</p></Card>
          )}

          <Card title="Deployments" padded={false}>
            <ol className="relative">
              {releases.map((r, i) => (
                <li key={r.id} className={`group relative flex items-center gap-4 px-5 py-3 transition hover:bg-zinc-50 dark:hover:bg-white/[0.03] ${r.id === shown?.id ? "bg-indigo-500/[0.06]" : ""}`}>
                  <div className="relative flex w-3 justify-center self-stretch">
                    {i < releases.length - 1 && <span className="absolute top-1/2 h-full w-px bg-zinc-200 dark:bg-white/[0.08]" />}
                    <span className="relative mt-[calc(50%-6px)]"><StatusDot status={r.status} size="sm" /></span>
                  </div>
                  <button onClick={() => setSelected(r.id)} className="flex min-w-0 flex-1 items-center gap-3 text-left">
                    <code className="text-xs text-zinc-500">{(r.sha ?? "").slice(0, 7) || "—"}</code>
                    <span className="truncate text-sm">{r.message || r.trigger}</span>
                  </button>
                  {r.id === app.current_release && <Badge tone="emerald">current</Badge>}
                  <span className="hidden text-xs text-zinc-500 sm:block">{timeAgo(r.created_at)}</span>
                  {r.path && r.status === "superseded" && (
                    <Button className="!h-7 opacity-0 transition group-hover:opacity-100" onClick={() => setConfirmRollback(r)}>Rollback</Button>
                  )}
                </li>
              ))}
              {releases.length === 0 && <li className="px-5 py-4 text-sm text-zinc-500">No deployment yet.</li>}
            </ol>
          </Card>
        </div>

        <div className="space-y-6">
          {app.mode === "managed" && <DomainCard key={app.domain ?? ""} app={app} onLinked={(rid) => { setSelected(rid); load(); }} />}
          <SettingsCard app={app} onSaved={(a) => setApp({ ...a, releases: app.releases })} />
          {app.mode === "managed" && <SupabaseCard app={app} onLinked={(rid) => { setSelected(rid); load(); }} />}
          {app.mode === "managed" && <EnvCard appId={app.id} />}
        </div>
      </div>

      <ConfirmDialog
        open={confirmRollback !== null}
        title={`Roll back to #${confirmRollback?.id ?? ""}?`}
        message={<>Commit <code>{(confirmRollback?.sha ?? "").slice(0, 7)}</code> goes back to production instantly; the current release stays available.</>}
        confirmLabel="Roll back"
        onConfirm={() => {
          const target = confirmRollback;
          setConfirmRollback(null);
          if (target) post("/rollback", { release_id: target.id });
        }}
        onCancel={() => setConfirmRollback(null)}
      />
      <ConfirmDialog
        open={confirmDelete}
        title={`Remove ${app.id}?`}
        message={
          app.mode === "adopted" ? "Mas stops deploying this service; the service itself is not touched." : (
            <span className="block space-y-2">
              <span className="block">The systemd unit and the nginx site are removed (backups are kept). The site goes offline.</span>
              <label className="flex items-center gap-2"><input type="checkbox" checked={purge} onChange={(e) => setPurge(e.target.checked)} /> Also delete /srv/mas/apps/{app.id}</label>
            </span>
          )
        }
        confirmLabel="Remove"
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
