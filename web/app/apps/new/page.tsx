"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { DomainPicker, type DomainChoice } from "@/components/DomainPicker";
import { Shell } from "@/components/Shell";
import { Button, Card, ErrorText, Input } from "@/components/ui";
import { api, ApiError, SERVICE_TYPES, type App, type Repo } from "@/lib/api";

const DEPLOYABLE = SERVICE_TYPES.filter((t) => t !== "unknown");
const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 24);

function NewApp() {
  const [repos, setRepos] = useState<Repo[] | null>(null);
  const [repo, setRepo] = useState<Repo | null>(null);
  const [branches, setBranches] = useState<string[]>([]);
  const [branch, setBranch] = useState("");
  const [type, setType] = useState("");
  const [id, setId] = useState("");
  const [withDomain, setWithDomain] = useState(true);
  const [domainChoice, setDomainChoice] = useState<DomainChoice | null>(null);
  const onDomain = useCallback((c: DomainChoice | null) => setDomainChoice(c), []);
  const [env, setEnv] = useState("");
  const [buildCommand, setBuildCommand] = useState("");
  const [startCommand, setStartCommand] = useState("");
  const [rootDir, setRootDir] = useState("");
  const [autoDeploy, setAutoDeploy] = useState(true);
  const [filter, setFilter] = useState("");
  const [detecting, setDetecting] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Repo[]>("/github/repos").then(setRepos).catch((e: ApiError) => setError(e.detail));
  }, []);

  useEffect(() => {
    if (!repo) return;
    setId(slug(repo.full_name.split("/")[1]));
    setBranch(repo.default_branch);
    api<string[]>(`/github/branches?repo=${encodeURIComponent(repo.full_name)}&installation_id=${repo.installation_id}`)
      .then(setBranches).catch(() => setBranches([repo.default_branch]));
  }, [repo]);

  useEffect(() => {
    if (!repo || !branch) return;
    setDetecting(true);
    api<{ type: string; start_command: string | null }>(
      `/github/detect?repo=${encodeURIComponent(repo.full_name)}&installation_id=${repo.installation_id}&ref=${encodeURIComponent(branch)}`,
    ).then((d) => {
      setType(d.type === "unknown" ? "" : d.type);
      if (d.start_command) setStartCommand(d.start_command);
    }).catch(() => {}).finally(() => setDetecting(false));
  }, [repo, branch]);

  const shown = useMemo(
    () => (repos ?? []).filter((r) => r.full_name.toLowerCase().includes(filter.toLowerCase())).slice(0, 50),
    [repos, filter],
  );

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!repo) return;
    if (withDomain && !domainChoice) {
      setError("Pick a valid domain, or untick “give it an address”.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const choice = withDomain ? domainChoice : null;
      if (choice?.zone) {
        await api("/dns/records", {
          method: "POST", body: { zone: choice.zone, subdomain: choice.subdomain, overwrite: choice.overwrite },
        });
      }
      const created = await api<App & { release_id: number }>("/apps", {
        method: "POST",
        body: {
          id, repo: repo.full_name, branch, installation_id: repo.installation_id, domain: choice?.domain ?? null, type, env,
          build_command: buildCommand || null, start_command: startCommand || null, root_dir: rootDir, auto_deploy: autoDeploy,
        },
      });
      window.location.href = `/apps/view/?id=${encodeURIComponent(created.id)}&release=${created.release_id}`;
    } catch (err) {
      setError((err as ApiError).detail.replace(/^points-elsewhere: |^cname: /, ""));
      setBusy(false);
    }
  }

  if (!repo) {
    return (
      <div className="space-y-4">
        <Link href="/apps/" className="text-sm text-zinc-500 hover:underline">← Apps</Link>
        <h1 className="text-xl font-semibold">New app · choose a repository</h1>
        <ErrorText>{error}</ErrorText>
        <Input placeholder="Filter repositories…" value={filter} onChange={(e) => setFilter(e.target.value)} autoFocus />
        <ul className="divide-y divide-zinc-200 overflow-hidden rounded-lg border border-zinc-200 bg-white dark:divide-zinc-800 dark:border-zinc-800 dark:bg-zinc-900">
          {shown.map((r) => (
            <li key={r.full_name}>
              <button onClick={() => setRepo(r)} className="flex w-full items-center justify-between px-4 py-2.5 text-left text-sm hover:bg-zinc-50 dark:hover:bg-zinc-800/40">
                <span className="font-medium">{r.full_name}</span>
                <span className="text-xs text-zinc-500">{r.private ? "private" : "public"} · {r.default_branch}</span>
              </button>
            </li>
          ))}
          {repos && shown.length === 0 && (
            <li className="px-4 py-6 text-center text-sm text-zinc-500">
              No repository. Install the GitHub App on your account or repositories (Settings → GitHub).
            </li>
          )}
          {!repos && !error && <li className="px-4 py-6 text-center text-sm text-zinc-500">Loading repositories…</li>}
        </ul>
      </div>
    );
  }

  const needsStart = type === "python";
  const servesFiles = type === "static" || type === "php";

  return (
    <form onSubmit={submit} className="space-y-4">
      <button type="button" onClick={() => setRepo(null)} className="text-sm text-zinc-500 hover:underline">← Repositories</button>
      <h1 className="text-xl font-semibold">New app · {repo.full_name}</h1>
      <Card title="Basics">
        <div className="grid gap-3 text-sm sm:grid-cols-2">
          <label className="space-y-1">
            <span className="text-zinc-500">App id</span>
            <Input value={id} onChange={(e) => setId(slug(e.target.value))} required />
            <span className="text-xs text-zinc-500">Folder /srv/mas/apps/{id || "…"}, user mas-{id || "…"}</span>
          </label>
          <label className="space-y-1">
            <span className="text-zinc-500">Branch</span>
            <select value={branch} onChange={(e) => setBranch(e.target.value)} className="w-full rounded-md border border-zinc-300 bg-white px-2 py-2 dark:border-zinc-700 dark:bg-zinc-950">
              {(branches.length ? branches : [branch]).map((b) => <option key={b}>{b}</option>)}
            </select>
          </label>
          <label className="space-y-1">
            <span className="text-zinc-500">Type {detecting && <span className="text-xs">(detecting…)</span>}</span>
            <select value={type} onChange={(e) => setType(e.target.value)} required className="w-full rounded-md border border-zinc-300 bg-white px-2 py-2 dark:border-zinc-700 dark:bg-zinc-950">
              <option value="" disabled>choose…</option>
              {DEPLOYABLE.map((t) => <option key={t}>{t}</option>)}
            </select>
          </label>
        </div>
      </Card>
      <Card title="Domain (optional)" actions={
        <label className="flex items-center gap-1.5 text-xs font-normal">
          <input type="checkbox" checked={withDomain} onChange={(e) => setWithDomain(e.target.checked)} /> give it an address
        </label>
      }>
        {withDomain ? (
          <DomainPicker suggestion={id} onChange={onDomain} />
        ) : (
          <p className="text-sm text-zinc-500">No domain: the app only listens on 127.0.0.1. You can add one later.</p>
        )}
      </Card>
      <Card title="Environment variables">
        <textarea
          value={env}
          onChange={(e) => setEnv(e.target.value)}
          rows={5}
          placeholder={"DATABASE_URL=postgres://…\nNEXT_PUBLIC_API=https://…"}
          className="mono w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-xs dark:border-zinc-700 dark:bg-zinc-950"
        />
        <p className="mt-1 text-xs text-zinc-500">Stored encrypted. Available at build time and at runtime.</p>
      </Card>
      <Card title="Build & run">
        <div className="grid gap-3 text-sm sm:grid-cols-2">
          <label className="space-y-1">
            <span className="text-zinc-500">Build command (optional)</span>
            <Input value={buildCommand} onChange={(e) => setBuildCommand(e.target.value)} placeholder="default: npm ci && npm run build / pip install" className="mono text-xs" />
          </label>
          <label className="space-y-1">
            <span className="text-zinc-500">Start command {needsStart ? "(required)" : "(optional)"}</span>
            <Input value={startCommand} onChange={(e) => setStartCommand(e.target.value)} required={needsStart} placeholder={needsStart ? ".venv/bin/uvicorn main:app --host 127.0.0.1 --port $PORT" : "default: next start / npm start"} className="mono text-xs" />
          </label>
          {servesFiles && (
            <label className="space-y-1">
              <span className="text-zinc-500">Folder to serve (optional)</span>
              <Input value={rootDir} onChange={(e) => setRootDir(e.target.value)} placeholder="public, dist, out…" className="mono text-xs" />
            </label>
          )}
          <label className="flex items-center gap-2 self-end pb-2">
            <input type="checkbox" checked={autoDeploy} onChange={(e) => setAutoDeploy(e.target.checked)} />
            <span>Deploy on every push to {branch}</span>
          </label>
        </div>
      </Card>
      <ErrorText>{error}</ErrorText>
      <Button variant="primary" type="submit" disabled={busy || !type}>{busy ? "Creating…" : "Create and deploy"}</Button>
    </form>
  );
}

export default function NewAppPage() {
  return (
    <Shell>
      <NewApp />
    </Shell>
  );
}
