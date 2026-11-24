"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { GitBranch, RefreshCw } from "lucide-react";
import { Badge, Button, Card, ConfirmDialog, CopyCode, ErrorText, Input, Label, Notice, Spinner } from "@/components/ui";
import { api, ApiError, timeAgo } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface Found { kind: "omnibus" | "docker"; name: string; url: string | null; image?: string; version?: string; server?: string }
interface Status {
  connected: boolean; url: string | null; webhook_url: string; error: string | null;
  user: { username: string; name: string; version: string | null } | null;
  detected: { local: Found[]; cluster: Found[] };
  backup?: { enabled: boolean; archives: { name: string; size: number }[]; running?: boolean; error?: string | null; result?: string | null };
}
interface MirrorState { project: string; at: number | null; error: string | null }

/** Settings → GitLab: the GitLab of this server or of the cluster found by itself, or any GitLab, connected with a token. */
export function GitLabCard() {
  const t = useT();
  const [status, setStatus] = useState<Status | null>(null);
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  const load = () => api<Status>("/gitlab").then((s) => { setStatus(s); if (!url && s.url) setUrl(s.url); }).catch((e: ApiError) => setError(e.detail));
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function connect(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(""); setMsg("");
    try {
      const r = await api<{ url: string; user: { username: string } }>("/gitlab", { method: "PUT", body: { url, token, password } });
      setMsg(t("Connected to {url} as {user}.", { url: r.url, user: r.user.username }));
      setToken(""); setPassword("");
      load();
    } catch (err) { setError((err as ApiError).detail); } finally { setBusy(false); }
  }
  async function disconnect() {
    await api("/gitlab", { method: "DELETE" }).catch(() => {});
    setMsg(""); load();
  }

  const found = [...(status?.detected.local ?? []), ...(status?.detected.cluster ?? [])];
  return (
    <Card title="GitLab" actions={status?.connected ? <Badge tone="emerald">{t("connected")}</Badge> : <Badge>{t("not connected")}</Badge>}>
      <div className="space-y-4 text-sm">
        <p className="text-zinc-500">{t("Deploy projects from a GitLab (yours or gitlab.com) like those of GitHub: each push deploys, each merge request gets its own preview, removed when it closes. Mas can also keep a copy of your GitHub repositories in it.")}</p>
        <Notice>{msg}</Notice>
        <ErrorText>{error}</ErrorText>
        {!status && <Spinner />}

        {status?.connected && (
          <div className="space-y-2">
            <p>{t("Connected to")} <a href={status.url!} target="_blank" rel="noreferrer" className="font-medium underline">{status.url}</a>
              {status.user && <> · {status.user.username}{status.user.version ? ` · GitLab ${status.user.version}` : ""}</>}</p>
            {status.error && <ErrorText>{status.error}</ErrorText>}
            <p className="text-xs text-zinc-500">{t("Mas adds the webhook to each project it deploys. To add it by hand (Settings → Webhooks of the project, push and merge request events):")}</p>
            <CopyCode text={status.webhook_url} />
            <Button variant="ghost" onClick={disconnect}>{t("Disconnect")}</Button>
          </div>
        )}

        {status && found.length > 0 && (
          <div className="space-y-2">
            <div className="font-medium">{t("Found on your servers")}</div>
            <ul className="space-y-1.5">
              {found.map((g, i) => (
                <li key={i} className="flex flex-wrap items-center gap-2 rounded-lg border border-zinc-200/70 px-3 py-2 dark:border-white/[0.06]">
                  <GitBranch className="size-4 text-orange-500" />
                  <span className="font-medium">{g.name}</span>
                  <Badge>{g.kind === "docker" ? "Docker" : t("package")}</Badge>
                  {g.version && <span className="text-xs text-zinc-500">{g.version}</span>}
                  <span className="text-xs text-zinc-500">{g.server ? t("on {server}", { server: g.server }) : t("on this server")}</span>
                  {g.url && <span className="mono text-xs">{g.url}</span>}
                  {g.url && !status.connected && <Button className="ml-auto" onClick={() => setUrl(g.url!)}>{t("Use it")}</Button>}
                </li>
              ))}
            </ul>
          </div>
        )}

        {status?.backup && (
          <div className="space-y-2 rounded-lg border border-zinc-200/70 p-3 dark:border-white/[0.06]">
            <label className="flex items-start gap-2">
              <input type="checkbox" className="mt-1" checked={status.backup.enabled}
                onChange={(e) => api("/gitlab/backup", { method: "PUT", body: { enabled: e.target.checked } }).then(load).catch((err: ApiError) => setError(err.detail))} />
              <span><span className="font-medium">{t("Back up the GitLab of this server every night")}</span>
                <span className="block text-xs text-zinc-500">{t("gitlab-backup (repositories, database, uploads) with gitlab-secrets.json and gitlab.rb, between 3 and 5 a.m.; the 3 latest are kept and sent to the remote storage of Mas's backups when it is set up. GitLab keeps running.")}</span></span>
            </label>
            <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500">
              <Button disabled={!!status.backup.running} onClick={() => api("/gitlab/backup/run", { method: "POST" }).then(load).catch((err: ApiError) => setError(err.detail))}>
                {status.backup.running ? <Spinner /> : null}{status.backup.running ? t("Backing up…") : t("Back up now")}
              </Button>
              {status.backup.archives.length > 0 && <span>{t("{n} backup(s), latest {name}", { n: status.backup.archives.length, name: status.backup.archives[0].name })}</span>}
            </div>
            {status.backup.error && <ErrorText>{status.backup.error}</ErrorText>}
          </div>
        )}

        {status && !status.connected && (
          <>
            {found.length === 0 && (
              <p className="text-zinc-500">{t("No GitLab found on this server or the cluster.")}{" "}
                <Link href="/apps/services/" className="underline">{t("Create one from the catalog")}</Link>{" "}
                {t("(about 3 GB of memory), then use its “Connect Mas to this GitLab” action: no token to copy.")}</p>
            )}
            <form onSubmit={connect} className="grid max-w-xl gap-3">
              <Label title={t("Address")}><Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://gitlab.example.com" required /></Label>
              <Label title={t("Access token")} hint={t("A personal (or group) access token with the api scope: GitLab → Preferences → Access tokens. Kept encrypted.")}>
                <Input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="glpat-…" autoComplete="off" required />
              </Label>
              <Label title={t("Your Mas password")}><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required /></Label>
              <div><Button variant="primary" disabled={busy}>{busy ? <Spinner /> : null}{t("Connect GitLab")}</Button></div>
            </form>
          </>
        )}
      </div>
    </Card>
  );
}

/** On an app deployed from GitHub: keep a copy of its repository on the connected GitLab. */
export function MirrorCard({ appId, repo }: { appId: string; repo: string }) {
  const t = useT();
  const [state, setState] = useState<{ mirror: MirrorState | null; gitlab: string | null } | null>(null);
  const [project, setProject] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [moving, setMoving] = useState(false);
  const load = () => api<{ mirror: MirrorState | null; gitlab: string | null }>(`/apps/${encodeURIComponent(appId)}/mirror`).then(setState).catch(() => setState(null));
  useEffect(() => { load(); setProject(`mirrors/${repo.split("/").pop() ?? appId}`); }, [appId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!state || !state.gitlab) return null;

  async function save(value: string | null) {
    setBusy(true); setError("");
    try { await api(`/apps/${encodeURIComponent(appId)}/mirror`, { method: "PUT", body: { project: value } }); await load(); }
    catch (err) { setError((err as ApiError).detail); } finally { setBusy(false); }
  }
  async function sync() {
    setBusy(true); setError("");
    try { await api(`/apps/${encodeURIComponent(appId)}/mirror/sync`, { method: "POST" }); await load(); }
    catch (err) { setError((err as ApiError).detail); } finally { setBusy(false); }
  }
  async function move() {
    setMoving(false); setBusy(true); setError("");
    try {
      await api(`/apps/${encodeURIComponent(appId)}/move-to-gitlab`, { method: "POST", body: { project: state?.mirror?.project ?? project.trim() } });
      window.location.reload();
    } catch (err) { setError((err as ApiError).detail); setBusy(false); }
  }
  const m = state.mirror;
  return (
    <Card title={t("GitLab mirror")} actions={m ? <Badge tone={m.error ? "red" : "emerald"}>{m.error ? t("failing") : t("in sync")}</Badge> : undefined}>
      <div className="space-y-3 text-sm">
        <p className="text-zinc-500">{t("Every branch and tag of {repo} copied to your GitLab after each push and every half hour: its CI and reviews work on it, and you keep a copy outside GitHub.", { repo })}</p>
        <ErrorText>{error}</ErrorText>
        {m ? (
          <>
            <p><a href={`${state.gitlab}/${m.project}`} target="_blank" rel="noreferrer" className="mono underline">{state.gitlab}/{m.project}</a>
              {m.at ? <span className="text-xs text-zinc-500"> · {t("synced {when}", { when: timeAgo(new Date(m.at * 1000).toISOString()) })}</span> : null}</p>
            {m.error && <ErrorText>{m.error}</ErrorText>}
            <div className="flex gap-2">
              <Button disabled={busy} onClick={sync}>{busy ? <Spinner /> : <RefreshCw className="size-3.5" />}{t("Sync now")}</Button>
              <Button variant="ghost" disabled={busy} onClick={() => save(null)}>{t("Stop mirroring")}</Button>
              <Button variant="ghost" disabled={busy} onClick={() => setMoving(true)}>{t("Deploy from GitLab instead")}</Button>
            </div>
          </>
        ) : (
          <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); save(project.trim()); }}>
            <Label title={t("GitLab project (group/name, created if missing)")}><Input value={project} onChange={(e) => setProject(e.target.value)} className="w-72" /></Label>
            <Button variant="primary" disabled={busy || !project.includes("/")}>{busy ? <Spinner /> : null}{t("Mirror to GitLab")}</Button>
          </form>
        )}
      </div>
      <ConfirmDialog open={moving} title={t("Deploy this app from GitLab?")}
        message={t("Mas copies the repository to GitLab one last time, checks that GitLab has the commit that is live, then follows GitLab: its pushes deploy, its merge requests get previews. Nothing is rebuilt and the site stays up. The previews of GitHub pull requests are removed; the GitHub repository is not touched.")}
        confirmLabel={t("Move to GitLab")} onConfirm={move} onCancel={() => setMoving(false)} />
    </Card>
  );
}
