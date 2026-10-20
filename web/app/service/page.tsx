"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { Badge, Button, Card, ConfirmDialog, ErrorText, Input, StatusDot } from "@/components/ui";
import {
  api, ApiError, daysUntil, githubUrl, SERVICE_TYPES, timeAgo,
  type ActionResult, type LogSource, type Service,
} from "@/lib/api";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  if (children === null || children === undefined || children === "" || children === false) return null;
  return (
    <div className="grid grid-cols-[8rem_1fr] gap-3 py-1.5 text-sm">
      <dt className="text-zinc-500">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

function Logs({ id }: { id: string }) {
  const [sources, setSources] = useState<LogSource[] | null>(null);
  const [active, setActive] = useState(0);
  const [lines, setLines] = useState(200);
  const [auto, setAuto] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api<{ sources: LogSource[] }>(`/services/${encodeURIComponent(id)}/logs?lines=${lines}`)
      .then((r) => { setSources(r.sources); setError(""); })
      .catch((e: ApiError) => setError(e.detail));
  }, [id, lines]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!auto) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [auto, load]);

  const src = sources?.[active];
  return (
    <Card
      title="Logs"
      actions={
        <>
          <select value={lines} onChange={(e) => setLines(Number(e.target.value))} className="rounded border border-zinc-300 bg-transparent px-1.5 py-1 text-xs dark:border-zinc-700">
            {[100, 200, 500, 1000].map((n) => <option key={n} value={n}>{n} lines</option>)}
          </select>
          <label className="flex items-center gap-1 text-xs text-zinc-500">
            <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> auto
          </label>
          <Button onClick={load} className="!px-2 !py-1 text-xs">Refresh</Button>
        </>
      }
    >
      {sources && sources.length > 1 && (
        <div className="mb-2 flex flex-wrap gap-1">
          {sources.map((s, i) => (
            <button key={s.name} onClick={() => setActive(i)} className={`mono rounded px-2 py-0.5 text-xs ${i === active ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900" : "bg-zinc-100 dark:bg-zinc-800"}`}>
              {s.name}
            </button>
          ))}
        </div>
      )}
      <ErrorText>{error || src?.error}</ErrorText>
      <pre className="mono max-h-[28rem] overflow-auto rounded-xl border border-zinc-800 bg-[#07080b] p-4 text-xs leading-relaxed text-zinc-300">
        {src ? (src.lines.length ? src.lines.join("\n") : "(empty)") : "Loading…"}
      </pre>
    </Card>
  );
}

function EditMeta({ svc, onSaved }: { svc: Service; onSaved: (s: Service) => void }) {
  const [displayName, setDisplayName] = useState(svc.display_name ?? "");
  const [notes, setNotes] = useState(svc.notes ?? "");
  const [type, setType] = useState<string>(svc.type_source === "manual" ? svc.type : "auto");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  async function save() {
    setSaving(true);
    setMsg("");
    try {
      const updated = await api<Service>(`/services/${encodeURIComponent(svc.id)}`, {
        method: "PATCH",
        body: { display_name: displayName, notes, type: type === "auto" ? null : type },
      });
      onSaved({ ...updated, history: svc.history });
      setMsg("Saved.");
    } catch (e) {
      setMsg((e as ApiError).detail);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card title="Label">
      <div className="space-y-3 text-sm">
        <label className="block space-y-1">
          <span className="text-zinc-500">Display name</span>
          <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder={svc.name} maxLength={100} />
        </label>
        <label className="block space-y-1">
          <span className="text-zinc-500">Type</span>
          <select value={type} onChange={(e) => setType(e.target.value)} className="w-full rounded-md border border-zinc-300 bg-white px-2 py-2 dark:border-zinc-700 dark:bg-zinc-950">
            <option value="auto">auto-detect</option>
            {SERVICE_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        <label className="block space-y-1">
          <span className="text-zinc-500">Notes</span>
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} maxLength={4000} className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 dark:border-zinc-700 dark:bg-zinc-950" />
        </label>
        <div className="flex items-center gap-3">
          <Button variant="primary" onClick={save} disabled={saving}>Save</Button>
          <span className="text-xs text-zinc-500">{msg}</span>
        </div>
      </div>
    </Card>
  );
}

function ServiceDetail() {
  const id = useSearchParams().get("id") ?? "";
  const [svc, setSvc] = useState<Service | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [result, setResult] = useState<ActionResult | null>(null);

  const load = useCallback(() => {
    api<Service>(`/services/${encodeURIComponent(id)}`).then(setSvc).catch((e: ApiError) => setError(e.detail));
  }, [id]);
  useEffect(() => { if (id) load(); }, [id, load]);

  async function run(action: string) {
    setConfirm(null);
    setPending(action);
    setResult(null);
    try {
      setResult(await api<ActionResult>(`/services/${encodeURIComponent(id)}/actions/${action}`, { method: "POST" }));
    } catch (e) {
      setResult({ ok: false, action, command: [], output: (e as ApiError).detail });
    } finally {
      setPending(null);
      setTimeout(load, 1500);
    }
  }

  async function adopt() {
    try {
      const created = await api<{ id: string }>("/apps/adopt", { method: "POST", body: { service_id: id } });
      window.location.href = `/apps/view/?id=${encodeURIComponent(created.id)}`;
    } catch (e) {
      setResult({ ok: false, action: "connect to GitHub", command: [], output: (e as ApiError).detail });
    }
  }

  if (!id) return <ErrorText>Missing service id.</ErrorText>;
  if (error) return <ErrorText>{error}</ErrorText>;
  if (!svc) return <p className="text-sm text-zinc-500">Loading…</p>;

  const gh = githubUrl(svc.git_remote);
  const sslDays = svc.ssl_expires ? daysUntil(svc.ssl_expires) : null;
  const label = (a: string) => a[0].toUpperCase() + a.slice(1);

  return (
    <div className="space-y-4">
      <Link href="/" className="text-sm text-zinc-500 hover:underline">← Services</Link>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <StatusDot status={svc.status} />
            <h1 className="text-[28px] font-semibold tracking-[-0.02em]">{svc.display_name || svc.name}</h1>
            <span className="text-sm text-zinc-500">{svc.status}</span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <Badge tone="sky">{svc.type}{svc.type_source === "manual" ? " (manual)" : ""}</Badge>
            <Badge>{svc.manager}</Badge>
            {svc.domains.map((d) => (
              <a key={d} href={`https://${d}`} target="_blank" rel="noreferrer" className="text-sm text-zinc-600 hover:underline dark:text-zinc-400">{d} ↗</a>
            ))}
            {svc.domains.length === 0 && <span className="text-sm italic text-zinc-500">no domain</span>}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {gh && svc.path && ["systemd", "pm2", "php-fpm", "nginx-static"].includes(svc.manager) && (
            <Button onClick={adopt} title="Deploy this service on every push, in place">Connect to GitHub</Button>
          )}
          {svc.actions.length === 0 && <span className="text-sm text-zinc-500">No action available for this manager.</span>}
          {svc.actions.map((a) => (
            <Button
              key={a}
              variant={a === "stop" ? "danger" : "secondary"}
              disabled={pending !== null}
              onClick={() => (svc.confirm_actions.includes(a) ? setConfirm(a) : run(a))}
            >
              {pending === a ? "…" : label(a)}
            </Button>
          ))}
        </div>
      </div>

      {result && (
        <div className={`rounded-md border px-3 py-2 text-sm ${result.ok ? "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200" : "border-red-300 bg-red-50 text-red-900 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200"}`}>
          <p className="font-medium">{result.action} {result.ok ? "succeeded" : "failed"}</p>
          {result.command.length > 0 && <p className="mono text-xs opacity-75">{result.command.join(" ")}</p>}
          {result.output && <pre className="mono mt-1 whitespace-pre-wrap text-xs">{result.output}</pre>}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card title="Details">
            <dl className="divide-y divide-zinc-100 dark:divide-zinc-800">
              <Row label="ID"><code className="text-xs">{svc.id}</code></Row>
              <Row label="Path">{svc.path && <code className="text-xs">{svc.path}</code>}</Row>
              <Row label="Port">{svc.port}</Row>
              <Row label="PID">{svc.pid}</Row>
              <Row label="User">{svc.user}</Row>
              <Row label="Unit">{svc.unit && <code className="text-xs">{svc.unit}</code>}</Row>
              <Row label="Upstream">{svc.upstream && <code className="text-xs">{svc.upstream}</code>}</Row>
              <Row label="Routes">{svc.routes.length > 0 && svc.routes.join(", ")}</Row>
              <Row label="SSL">
                {svc.ssl_expires && (
                  <span className={sslDays !== null && sslDays < 14 ? "text-amber-600 dark:text-amber-400" : ""}>
                    expires {svc.ssl_expires.slice(0, 10)} ({sslDays} days){svc.https_redirect ? " · HTTP→HTTPS redirect" : ""}
                  </span>
                )}
              </Row>
              <Row label="nginx">{svc.nginx_files.length > 0 && <code className="text-xs">{svc.nginx_files.join(", ")}</code>}</Row>
              <Row label="Command">{svc.cmdline && <code className="break-all text-xs">{svc.cmdline}</code>}</Row>
              <Row label="Git">
                {svc.git_remote && (
                  <span>
                    {gh ? <a href={gh} target="_blank" rel="noreferrer" className="hover:underline">{gh.replace("https://github.com/", "")}</a> : <code className="text-xs">{svc.git_remote}</code>}
                    {svc.git_branch && <span className="text-zinc-500"> @ {svc.git_branch}</span>}
                  </span>
                )}
              </Row>
              <Row label="Last commit">
                {svc.last_commit && (
                  <span>
                    <code className="text-xs">{svc.last_commit.sha.slice(0, 7)}</code> {svc.last_commit.subject}
                    {svc.last_commit.date && <span className="text-zinc-500"> · {timeAgo(svc.last_commit.date)}</span>}
                  </span>
                )}
              </Row>
            </dl>
          </Card>
          <Logs id={svc.id} />
        </div>
        <div className="space-y-4">
          <EditMeta svc={svc} onSaved={setSvc} />
          <Card title="History">
            {(svc.history ?? []).length === 0 && <p className="text-sm text-zinc-500">No action yet.</p>}
            <ul className="space-y-2 text-sm">
              {(svc.history ?? []).map((h) => (
                <li key={h.id} className="flex items-baseline justify-between gap-2">
                  <span>
                    <span className={h.ok ? "text-emerald-600" : "text-red-600"}>{h.ok ? "✓" : "✗"}</span> {h.action}
                    <span className="text-zinc-500"> by {h.actor}</span>
                  </span>
                  <span className="shrink-0 text-xs text-zinc-500" title={h.at}>{timeAgo(h.at)}</span>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>

      <ConfirmDialog
        open={confirm !== null}
        title={`${confirm ? label(confirm) : ""} ${svc.display_name || svc.name}?`}
        message={confirm === "stop" ? "The service will be unavailable until it is started again." : "The service will be briefly unavailable."}
        confirmLabel={confirm ? label(confirm) : ""}
        danger={confirm === "stop"}
        onConfirm={() => confirm && run(confirm)}
        onCancel={() => setConfirm(null)}
      />
    </div>
  );
}

export default function ServicePage() {
  return (
    <Shell>
      <Suspense fallback={<p className="text-sm text-zinc-500">Loading…</p>}>
        <ServiceDetail />
      </Suspense>
    </Shell>
  );
}
