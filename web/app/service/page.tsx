"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { Badge, Button, Card, ConfirmDialog, CopyCode, ErrorText, Input, StatusDot } from "@/components/ui";
import {
  api, ApiError, daysUntil, githubUrl, SERVICE_TYPES, timeAgo,
  type ActionResult, type LogSource, type Service, type ServiceRuntime,
} from "@/lib/api";
import { msg, useT } from "@/lib/i18n";

const ACTION_LABELS: Record<string, string> = {
  start: msg("Start"), stop: msg("Stop"), restart: msg("Restart"), reload: msg("Reload"),
  enable: msg("Start at boot"), disable: msg("Do not start at boot"),
};
const BOOT_ACTIONS = ["enable", "disable"];  // shown in the live card, not with start / stop

const mb = (n: number | null | undefined) => (n === null || n === undefined ? null : n >= 1024 ? `${(n / 1024).toFixed(2)} GB` : `${Math.round(n)} MB`);
const STATUS_LABELS: Record<string, string> = {
  running: msg("running"), stopped: msg("stopped"), errored: msg("errored"), unknown: msg("unknown"),
};

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
  const t = useT();
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
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [auto, load]);

  const src = sources?.[active];
  return (
    <Card
      title={t("Logs")}
      actions={
        <>
          <select value={lines} onChange={(e) => setLines(Number(e.target.value))} className="rounded border border-zinc-300 bg-transparent px-1.5 py-1 text-xs dark:border-zinc-700">
            {[100, 200, 500, 1000].map((n) => <option key={n} value={n}>{t("{n} lines", { n })}</option>)}
          </select>
          <label className="flex items-center gap-1 text-xs text-zinc-500">
            <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> {t("auto")}
          </label>
          <Button onClick={load} className="!px-2 !py-1 text-xs">{t("Refresh")}</Button>
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
        {src ? (src.lines.length ? src.lines.join("\n") : t("(empty)")) : t("Loading…")}
      </pre>
    </Card>
  );
}

/** Memory, CPU, uptime, restarts and boot start, read every 10 s. */
function Live({ svc, onRuntime, onBoot }: { svc: Service; onRuntime: (r: ServiceRuntime) => void; onBoot: (action: string) => void }) {
  const t = useT();
  const [rt, setRt] = useState<ServiceRuntime | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    api<ServiceRuntime>(`/services/${encodeURIComponent(svc.id)}/runtime`)
      .then((r) => { setRt(r); onRuntime(r); setError(""); })
      .catch((e: ApiError) => setError(e.detail));
  }, [svc.id, onRuntime]);
  useEffect(() => {
    load();
    const timer = setInterval(load, 10_000);
    return () => clearInterval(timer);
  }, [load]);

  const p = rt?.process, sd = rt?.systemd, dk = rt?.docker;
  const memory = sd?.memory_mb ?? dk?.memory_mb ?? p?.memory_mb;
  const cpu = dk?.cpu_percent ?? p?.cpu_percent;
  const started = sd?.started_at ?? dk?.started_at ?? p?.started_at;
  const restarts = sd?.restarts ?? dk?.restarts;
  const boot = sd?.enabled;
  return (
    <Card title={t("Right now")} actions={<span className="text-xs text-zinc-500">{t("every 10 s")}</span>}>
      <ErrorText>{error}</ErrorText>
      {!rt && !error && <p className="text-sm text-zinc-500">{t("Loading…")}</p>}
      {rt && !p && !sd && !dk && (
        <p className="text-sm text-zinc-500">{svc.manager === "nginx-static" ? t("A static site: nginx serves its files, no process of its own to measure.") : t("No running process found for this service.")}</p>
      )}
      {rt && (p || sd || dk) && (
        <dl className="divide-y divide-zinc-100 dark:divide-zinc-800">
          <Row label={t("Memory")}>{mb(memory) && <span>{mb(memory)}{sd?.memory_peak_mb ? <span className="text-zinc-500"> · {t("peak {v}", { v: mb(sd.memory_peak_mb) ?? "" })}</span> : null}{dk?.memory_limit_mb ? <span className="text-zinc-500"> / {mb(dk.memory_limit_mb)}</span> : null}</span>}</Row>
          <Row label="CPU">{cpu !== null && cpu !== undefined ? <span>{cpu} %{sd?.cpu_seconds ? <span className="text-zinc-500"> · {t("{n} s in total", { n: sd.cpu_seconds })}</span> : null}</span> : null}</Row>
          <Row label={t("Started")}>{started && <span title={started}>{timeAgo(started)}</span>}</Row>
          <Row label={t("Restarts")}>{restarts !== null && restarts !== undefined && String(restarts)}</Row>
          <Row label={t("Processes")}>{p && t("{n} process(es), {threads} threads", { n: p.processes, threads: p.threads })}</Row>
          <Row label={t("Image")}>{dk?.image && <code className="text-xs">{dk.image}</code>}</Row>
          <Row label={t("Health")}>{dk?.health}</Row>
          <Row label={t("Restart policy")}>{dk?.restart_policy}</Row>
          <Row label={t("Description")}>{sd?.description}</Row>
          <Row label={t("Unit file")}>{sd?.unit_file && <code className="text-xs">{sd.unit_file}</code>}</Row>
          <Row label={t("At boot")}>
            {boot && (
              <span className="flex flex-wrap items-center gap-2">
                <span>{boot === "enabled" ? t("starts at boot") : boot === "disabled" ? t("does not start at boot") : boot}</span>
                {boot === "disabled" && svc.actions.includes("enable") && <Button className="!px-2 !py-1 text-xs" onClick={() => onBoot("enable")}>{t("Start at boot")}</Button>}
                {boot === "enabled" && svc.actions.includes("disable") && <Button className="!px-2 !py-1 text-xs" onClick={() => onBoot("disable")}>{t("Do not start at boot")}</Button>}
              </span>
            )}
          </Row>
        </dl>
      )}
    </Card>
  );
}

/** Where to open the service: its sites, public ports, an SSH tunnel, the Mas app, the commit on GitHub. */
function Links({ svc, rt }: { svc: Service; rt: ServiceRuntime | null }) {
  const t = useT();
  const gh = githubUrl(svc.git_remote);
  if (!rt) return null;
  const tunnels = rt.links.filter((l) => l.kind === "tunnel");
  const opens = rt.links.filter((l) => l.kind !== "tunnel");
  if (!rt.links.length && !rt.app && !gh && !rt.ports.length) return null;
  return (
    <Card title={t("Links")}>
      <div className="space-y-3 text-sm">
        {opens.length > 0 && (
          <ul className="space-y-1">
            {opens.map((l) => (
              <li key={l.url}><a href={l.url} target="_blank" rel="noreferrer" className="break-all text-sky-600 hover:underline dark:text-sky-400">{l.url} ↗</a></li>
            ))}
          </ul>
        )}
        {tunnels.map((l) => (
          <div key={l.url} className="space-y-1">
            <p className="text-zinc-500">{t("Port {port} only listens on the server. From your computer:", { port: l.port ?? "" })}</p>
            <CopyCode text={l.command ?? ""} />
            <p className="text-zinc-500">{t("then open {url}", { url: l.url })}</p>
          </div>
        ))}
        {rt.app && <p><Link href={`/apps/view/?id=${encodeURIComponent(rt.app.id)}`} className="text-sky-600 hover:underline dark:text-sky-400">{t("Mas app: {name}", { name: rt.app.name })} →</Link></p>}
        {gh && (
          <p className="flex flex-wrap gap-3">
            <a href={gh} target="_blank" rel="noreferrer" className="text-sky-600 hover:underline dark:text-sky-400">GitHub ↗</a>
            {svc.last_commit && <a href={`${gh}/commit/${svc.last_commit.sha}`} target="_blank" rel="noreferrer" className="text-sky-600 hover:underline dark:text-sky-400">{t("Commit {sha}", { sha: svc.last_commit.sha.slice(0, 7) })} ↗</a>}
          </p>
        )}
        {rt.ports.length > 0 && (
          <div>
            <p className="text-xs text-zinc-500">{t("Listening ports")}</p>
            <ul className="mt-1 flex flex-wrap gap-1.5">
              {rt.ports.map((p) => (
                <li key={`${p.address}:${p.port}`}><Badge tone={p.public ? "amber" : undefined}>{p.port}{p.public ? ` · ${t("public")}` : ` · ${t("local")}`}</Badge></li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Card>
  );
}

function EditMeta({ svc, onSaved }: { svc: Service; onSaved: (s: Service) => void }) {
  const t = useT();
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
      setMsg(t("Saved."));
    } catch (e) {
      setMsg((e as ApiError).detail);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card title={t("Label")}>
      <div className="space-y-3 text-sm">
        <label className="block space-y-1">
          <span className="text-zinc-500">{t("Display name")}</span>
          <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder={svc.name} maxLength={100} />
        </label>
        <label className="block space-y-1">
          <span className="text-zinc-500">{t("Type")}</span>
          <select value={type} onChange={(e) => setType(e.target.value)} className="w-full rounded-md border border-zinc-300 bg-white px-2 py-2 dark:border-zinc-700 dark:bg-zinc-950">
            <option value="auto">{t("auto-detect")}</option>
            {SERVICE_TYPES.map((type) => <option key={type} value={type}>{type}</option>)}
          </select>
        </label>
        <label className="block space-y-1">
          <span className="text-zinc-500">{t("Notes")}</span>
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} maxLength={4000} className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 dark:border-zinc-700 dark:bg-zinc-950" />
        </label>
        <div className="flex items-center gap-3">
          <Button variant="primary" onClick={save} disabled={saving}>{t("Save")}</Button>
          <span className="text-xs text-zinc-500">{msg}</span>
        </div>
      </div>
    </Card>
  );
}

function ServiceDetail() {
  const t = useT();
  const id = useSearchParams().get("id") ?? "";
  const [svc, setSvc] = useState<Service | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [result, setResult] = useState<ActionResult | null>(null);
  const [rt, setRt] = useState<ServiceRuntime | null>(null);

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
      setResult({ ok: false, action: t("Connect to GitHub"), command: [], output: (e as ApiError).detail });
    }
  }

  if (!id) return <ErrorText>{t("Missing service id.")}</ErrorText>;
  if (error) return <ErrorText>{error}</ErrorText>;
  if (!svc) return <p className="text-sm text-zinc-500">{t("Loading…")}</p>;

  const gh = githubUrl(svc.git_remote);
  const sslDays = svc.ssl_expires ? daysUntil(svc.ssl_expires) : null;
  const label = (a: string) => (ACTION_LABELS[a] ? t(ACTION_LABELS[a]) : a[0].toUpperCase() + a.slice(1));

  return (
    <div className="space-y-4">
      <Link href="/" className="text-sm text-zinc-500 hover:underline">← {t("Services")}</Link>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <StatusDot status={svc.status} />
            <h1 className="text-[28px] font-semibold tracking-[-0.02em]">{svc.display_name || svc.name}</h1>
            <span className="text-sm text-zinc-500">{t(STATUS_LABELS[svc.status] ?? svc.status)}</span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <Badge tone="sky">{svc.type}{svc.type_source === "manual" ? ` (${t("manual")})` : ""}</Badge>
            <Badge>{svc.manager}</Badge>
            {svc.domains.map((d) => (
              <a key={d} href={`https://${d}`} target="_blank" rel="noreferrer" className="text-sm text-zinc-600 hover:underline dark:text-zinc-400">{d} ↗</a>
            ))}
            {svc.domains.length === 0 && <span className="text-sm italic text-zinc-500">{t("no domain")}</span>}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {gh && svc.path && ["systemd", "pm2", "php-fpm", "nginx-static"].includes(svc.manager) && (
            <Button onClick={adopt} title={t("Deploy this service on every push, in place")}>{t("Connect to GitHub")}</Button>
          )}
          {svc.app && (
            <Link href={`/apps/view/?id=${encodeURIComponent(svc.app.id)}`} className="inline-flex h-9 items-center rounded-lg border border-violet-400/40 px-3 text-sm font-medium text-violet-600 hover:bg-violet-500/10 dark:text-violet-300">
              {t("Mas app: {name}", { name: svc.app.name })} →
            </Link>
          )}
          {svc.domains.length > 0 && svc.nginx_files.length > 0 && !svc.app && !rt?.app && (
            <Link href={`/apps/new/?replace=${encodeURIComponent(svc.id)}`} className="inline-flex h-9 items-center rounded-lg bg-gradient-to-r from-indigo-500 via-sky-500 to-fuchsia-500 px-3 text-sm font-medium text-white hover:brightness-110" title={t("A Mas app (deployed from GitHub, CMS) takes over its domains without interruption")}>
              {t("Replace with a Mas app")}
            </Link>
          )}
          {svc.actions.length === 0 && <span className="text-sm text-zinc-500">{t("No action available for this manager.")}</span>}
          {svc.actions.filter((a) => !BOOT_ACTIONS.includes(a)).map((a) => (
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
          <p className="font-medium">{result.ok ? t("{action} succeeded", { action: label(result.action) }) : t("{action} failed", { action: label(result.action) })}</p>
          {result.command.length > 0 && <p className="mono text-xs opacity-75">{result.command.join(" ")}</p>}
          {result.output && <pre className="mono mt-1 whitespace-pre-wrap text-xs">{result.output}</pre>}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card title={t("Details")}>
            <dl className="divide-y divide-zinc-100 dark:divide-zinc-800">
              <Row label="ID"><code className="text-xs">{svc.id}</code></Row>
              <Row label={t("Path")}>{svc.path && <code className="text-xs">{svc.path}</code>}</Row>
              <Row label="Port">{svc.port}</Row>
              <Row label="PID">{svc.pid}</Row>
              <Row label={t("User")}>{svc.user}</Row>
              <Row label="Unit">{svc.unit && <code className="text-xs">{svc.unit}</code>}</Row>
              <Row label="Upstream">{svc.upstream && <code className="text-xs">{svc.upstream}</code>}</Row>
              <Row label={t("Routes")}>{svc.routes.length > 0 && svc.routes.join(", ")}</Row>
              <Row label="SSL">
                {svc.ssl_expires && (
                  <span className={sslDays !== null && sslDays < 14 ? "text-amber-600 dark:text-amber-400" : ""}>
                    {t("expires {date} ({n} days)", { date: svc.ssl_expires.slice(0, 10), n: sslDays ?? 0 })}{svc.https_redirect ? ` · ${t("HTTP→HTTPS redirect")}` : ""}
                  </span>
                )}
              </Row>
              <Row label="nginx">{svc.nginx_files.length > 0 && <code className="text-xs">{svc.nginx_files.join(", ")}</code>}</Row>
              <Row label={t("Command")}>{svc.cmdline && <code className="break-all text-xs">{svc.cmdline}</code>}</Row>
              <Row label="Git">
                {svc.git_remote && (
                  <span>
                    {gh ? <a href={gh} target="_blank" rel="noreferrer" className="hover:underline">{gh.replace("https://github.com/", "")}</a> : <code className="text-xs">{svc.git_remote}</code>}
                    {svc.git_branch && <span className="text-zinc-500"> @ {svc.git_branch}</span>}
                  </span>
                )}
              </Row>
              <Row label={t("Last commit")}>
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
          <Live svc={svc} onRuntime={setRt} onBoot={(a) => (svc.confirm_actions.includes(a) ? setConfirm(a) : run(a))} />
          <Links svc={svc} rt={rt} />
          <EditMeta svc={svc} onSaved={setSvc} />
          <Card title={t("History")}>
            {(svc.history ?? []).length === 0 && <p className="text-sm text-zinc-500">{t("No action yet.")}</p>}
            <ul className="space-y-2 text-sm">
              {(svc.history ?? []).map((h) => (
                <li key={h.id} className="flex items-baseline justify-between gap-2">
                  <span>
                    <span className={h.ok ? "text-emerald-600" : "text-red-600"}>{h.ok ? "✓" : "✗"}</span> {h.action}
                    <span className="text-zinc-500"> {t("by {who}", { who: h.actor })}</span>
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
        message={confirm === "stop" ? t("The service will be unavailable until it is started again.") : confirm === "disable" ? t("The service keeps running now, but will not start again after a reboot of the server.") : t("The service will be briefly unavailable.")}
        confirmLabel={confirm ? label(confirm) : ""}
        danger={confirm === "stop" || confirm === "disable"}
        onConfirm={() => confirm && run(confirm)}
        onCancel={() => setConfirm(null)}
      />
    </div>
  );
}

export default function ServicePage() {
  return (
    <Shell>
      <Suspense fallback={null}>
        <ServiceDetail />
      </Suspense>
    </Shell>
  );
}
