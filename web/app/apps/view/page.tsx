"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { DomainPicker, type DomainChoice } from "@/components/DomainPicker";
import { Shell } from "@/components/Shell";
import { Badge, Button, Card, ConfirmDialog, ErrorText, Input, ReleaseBadge } from "@/components/ui";
import { api, ApiError, timeAgo, type App, type Release } from "@/lib/api";

function BuildLog({ appId, release, onEnd }: { appId: string; release: Release; onEnd: () => void }) {
  const [lines, setLines] = useState<string[]>([]);
  const box = useRef<HTMLPreElement>(null);

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
    <pre ref={box} className="mono max-h-[30rem] min-h-[8rem] overflow-auto rounded-md bg-zinc-950 p-3 text-xs leading-relaxed text-zinc-200">
      {lines.length ? lines.join("\n") : "Waiting for output…"}
    </pre>
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
      const r = await api<{ release_id: number; changes: string[] }>(`/apps/${encodeURIComponent(app.id)}/domain`, {
        method: "POST",
        body: choice.zone
          ? { zone: choice.zone, subdomain: choice.subdomain, overwrite: choice.overwrite }
          : { domain: choice.domain },
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
        <div className="space-y-2 text-sm">
          <a href={`https://${app.domain}`} target="_blank" rel="noreferrer" className="font-medium hover:underline">{app.domain} ↗</a>
          <div><Button onClick={() => setEditing(true)}>Change</Button></div>
        </div>
      ) : (
        <div className="space-y-3">
          {!app.domain && <p className="text-sm text-zinc-500">This app has no address yet. Pick one: Mas creates the DNS record, the nginx site and the HTTPS certificate.</p>}
          <DomainPicker suggestion={app.id} onChange={onChange} />
          <ErrorText>{error}</ErrorText>
          <div className="flex gap-2">
            <Button variant="primary" onClick={link} disabled={!choice || busy}>{busy ? "Linking…" : "Link domain"}</Button>
            {app.domain && <Button onClick={() => setEditing(false)}>Cancel</Button>}
          </div>
        </div>
      )}
    </Card>
  );
}

function Settings({ app, onSaved }: { app: App; onSaved: (a: App) => void }) {
  const [branch, setBranch] = useState(app.branch);
  const [buildCommand, setBuildCommand] = useState(app.build_command ?? "");
  const [startCommand, setStartCommand] = useState(app.start_command ?? "");
  const [autoDeploy, setAutoDeploy] = useState(app.auto_deploy);
  const [msg, setMsg] = useState("");

  async function save() {
    setMsg("");
    const body: Record<string, unknown> = { branch, build_command: buildCommand, auto_deploy: autoDeploy };
    if (app.mode === "managed") Object.assign(body, { start_command: startCommand });
    try {
      onSaved(await api<App>(`/apps/${encodeURIComponent(app.id)}`, { method: "PATCH", body }));
      setMsg("Saved. Deploy to apply.");
    } catch (e) {
      setMsg((e as ApiError).detail);
    }
  }

  return (
    <Card title="Settings">
      <div className="space-y-3 text-sm">
        <label className="block space-y-1"><span className="text-zinc-500">Branch</span><Input value={branch} onChange={(e) => setBranch(e.target.value)} /></label>
        <label className="block space-y-1"><span className="text-zinc-500">Build command</span><Input value={buildCommand} onChange={(e) => setBuildCommand(e.target.value)} placeholder="default" className="mono text-xs" /></label>
        {app.mode === "managed" && (
          <label className="block space-y-1"><span className="text-zinc-500">Start command</span><Input value={startCommand} onChange={(e) => setStartCommand(e.target.value)} placeholder="default" className="mono text-xs" /></label>
        )}
        <label className="flex items-center gap-2"><input type="checkbox" checked={autoDeploy} onChange={(e) => setAutoDeploy(e.target.checked)} /> Deploy on push</label>
        <div className="flex items-center gap-3"><Button variant="primary" onClick={save}>Save</Button><span className="text-xs text-zinc-500">{msg}</span></div>
      </div>
    </Card>
  );
}

function EnvEditor({ appId }: { appId: string }) {
  const [text, setText] = useState<string | null>(null);
  const [msg, setMsg] = useState("");

  async function reveal() {
    try {
      setText((await api<{ text: string }>(`/apps/${encodeURIComponent(appId)}/env`)).text);
    } catch (e) {
      setMsg((e as ApiError).detail);
    }
  }

  async function save() {
    try {
      const r = await api<{ count: number }>(`/apps/${encodeURIComponent(appId)}/env`, { method: "PUT", body: { text: text ?? "" } });
      setMsg(`${r.count} variable(s) saved. Deploy to apply.`);
    } catch (e) {
      setMsg((e as ApiError).detail);
    }
  }

  return (
    <Card title="Environment variables">
      {text === null ? (
        <Button onClick={reveal}>Show and edit</Button>
      ) : (
        <div className="space-y-2">
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={6} className="mono w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-xs dark:border-zinc-700 dark:bg-zinc-950" />
          <Button variant="primary" onClick={save}>Save</Button>
        </div>
      )}
      {msg && <p className="mt-2 text-xs text-zinc-500">{msg}</p>}
    </Card>
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
  if (!app) return <p className="text-sm text-zinc-500">Loading…</p>;

  const releases = app.releases ?? [];
  const shownRelease = releases.find((r) => r.id === selected) ?? releases[0];

  return (
    <div className="space-y-4">
      <Link href="/apps/" className="text-sm text-zinc-500 hover:underline">← Apps</Link>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">{app.id}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-sm">
            <Badge tone="sky">{app.type}</Badge>
            {app.mode === "adopted" && <Badge>adopted · {app.path}</Badge>}
            <a href={`https://github.com/${app.repo}`} target="_blank" rel="noreferrer" className="text-zinc-600 hover:underline dark:text-zinc-400">{app.repo}@{app.branch}</a>
            {app.domain && <a href={`https://${app.domain}`} target="_blank" rel="noreferrer" className="text-zinc-600 hover:underline dark:text-zinc-400">{app.domain} ↗</a>}
            {app.port && <span className="mono text-xs text-zinc-500">:{app.port}</span>}
          </div>
        </div>
        <div className="flex gap-2">
          <Button variant="primary" onClick={() => post("/deploy")}>Deploy now</Button>
          <Button variant="danger" onClick={() => setConfirmDelete(true)}>Remove</Button>
        </div>
      </div>
      <ErrorText>{error}</ErrorText>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card title={shownRelease ? <span className="flex items-center gap-2">Release #{shownRelease.id} <ReleaseBadge status={shownRelease.status} /></span> : "Build log"}>
            {shownRelease ? (
              <>
                <p className="mb-2 text-xs text-zinc-500">
                  <code>{(shownRelease.sha ?? "").slice(0, 7)}</code> {shownRelease.message} · {shownRelease.trigger}
                  {shownRelease.actor ? ` by ${shownRelease.actor}` : ""} · {timeAgo(shownRelease.created_at)}
                </p>
                {shownRelease.error && <p className="mb-2 whitespace-pre-wrap text-sm text-red-600 dark:text-red-400">{shownRelease.error}</p>}
                <BuildLog appId={app.id} release={shownRelease} onEnd={load} />
              </>
            ) : (
              <p className="text-sm text-zinc-500">No deployment yet. Click “Deploy now”.</p>
            )}
          </Card>
          <Card title="Releases">
            <ul className="divide-y divide-zinc-100 text-sm dark:divide-zinc-800">
              {releases.map((r) => (
                <li key={r.id} className={`flex flex-wrap items-center gap-2 py-2 ${r.id === shownRelease?.id ? "font-medium" : ""}`}>
                  <button onClick={() => setSelected(r.id)} className="flex min-w-0 flex-1 items-center gap-2 text-left hover:underline">
                    <span className="text-zinc-500">#{r.id}</span>
                    <ReleaseBadge status={r.status} />
                    <code className="text-xs">{(r.sha ?? "").slice(0, 7)}</code>
                    <span className="truncate">{r.message}</span>
                  </button>
                  <span className="text-xs text-zinc-500">{timeAgo(r.created_at)}</span>
                  {r.path && r.status === "superseded" && (
                    <Button className="!px-2 !py-0.5 text-xs" onClick={() => setConfirmRollback(r)}>Rollback</Button>
                  )}
                </li>
              ))}
              {releases.length === 0 && <li className="py-2 text-zinc-500">No release yet.</li>}
            </ul>
          </Card>
        </div>
        <div className="space-y-4">
          {app.mode === "managed" && (
            <DomainCard key={app.domain ?? ""} app={app} onLinked={(rid) => { setSelected(rid); load(); }} />
          )}
          <Settings app={app} onSaved={(a) => setApp({ ...a, releases: app.releases })} />
          {app.mode === "managed" && <EnvEditor appId={app.id} />}
        </div>
      </div>

      <ConfirmDialog
        open={confirmRollback !== null}
        title={`Roll back to release #${confirmRollback?.id ?? ""}?`}
        message={<>Commit <code>{(confirmRollback?.sha ?? "").slice(0, 7)}</code> goes back to production; the current release stays available.</>}
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
              <span className="block">The systemd unit and the nginx vhost are removed (backups are kept). The site goes offline.</span>
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
      <Suspense fallback={<p className="text-sm text-zinc-500">Loading…</p>}>
        <AppView />
      </Suspense>
    </Shell>
  );
}
