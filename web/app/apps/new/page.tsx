"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { DomainPicker, type DomainChoice } from "@/components/DomainPicker";
import { Shell } from "@/components/Shell";
import { Badge, Button, Card, ErrorText, Input, PageHeader } from "@/components/ui";
import { api, ApiError, SERVICE_TYPES, type App, type CmsCompat, type Repo } from "@/lib/api";
import { useT } from "@/lib/i18n";

const DEPLOYABLE = SERVICE_TYPES.filter((type) => type !== "unknown");
function CompatBadge({ compat }: { compat?: CmsCompat }) {
  const t = useT();
  if (!compat || (!compat.compatible && !compat.error)) return null;
  if (!compat.compatible) return <span title={compat.error ?? ""}><Badge tone="amber">{t("invalid mas.cms.json")}</Badge></span>;
  const what = [
    compat.pages ? t("{n} page(s)", { n: compat.pages }) : "",
    compat.collections ? t("{n} collection(s)", { n: compat.collections }) : "",
    compat.storage.includes("supabase") ? "Supabase" : "",
  ].filter(Boolean).join(" · ");
  return <span title={what}><Badge tone="emerald">{t("Mas compatible")}</Badge></span>;
}

interface RepoDatabase {
  instance: string | null;
  uses_supabase: boolean;
  evidence: string[];
  migrations: { file: string }[];
  seed: boolean;
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 24);

function NewApp() {
  const t = useT();
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
  const [db, setDb] = useState<RepoDatabase | null>(null);
  const [connectDb, setConnectDb] = useState(true);
  const [seedDb, setSeedDb] = useState(true);
  const [compat, setCompat] = useState<Record<string, CmsCompat>>({});

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

  // Badge "Mas compatible" on the repositories shown (a valid mas.cms.json on their default branch).
  useEffect(() => {
    const missing = shown.filter((r) => !(r.full_name in compat));
    if (!missing.length) return;
    const timer = setTimeout(() => {
      api<Record<string, CmsCompat>>("/cms/compatibility", {
        method: "POST",
        body: { repos: missing.map((r) => ({ full_name: r.full_name, installation_id: r.installation_id, default_branch: r.default_branch })) },
      }).then((found) => setCompat((c) => ({ ...c, ...found }))).catch(() => {});
    }, 300);
    return () => clearTimeout(timer);
  }, [shown, compat]);

  // Does the repository use Supabase? (libraries, .env.example, supabase/migrations)
  useEffect(() => {
    setDb(null);
    if (!repo || !branch) return;
    const timer = setTimeout(() => {
      const q = `repo=${encodeURIComponent(repo.full_name)}&installation_id=${repo.installation_id}&ref=${encodeURIComponent(branch)}&root_dir=${encodeURIComponent(rootDir)}`;
      api<RepoDatabase>(`/github/database?${q}`).then(setDb).catch(() => {});
    }, 400);
    return () => clearTimeout(timer);
  }, [repo, branch, rootDir]);
  const canConnect = !!db?.uses_supabase && (db.instance === "running" || db.instance === "installing");

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!repo) return;
    if (withDomain && !domainChoice) {
      setError(t("Pick a valid domain, or untick “give it an address”."));
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
          supabase: canConnect && connectDb, supabase_seed: seedDb,
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
        <Link href="/apps/" className="text-sm text-zinc-500 hover:underline">← {t("Apps")}</Link>
        <PageHeader eyebrow={t("New app")} title={<>{t("Import a")} <span className="text-gradient">{t("Git repository")}</span></>} subtitle={t("Every push to the branch you pick deploys automatically.")} />
        <ErrorText>{error}</ErrorText>
        <Input placeholder={t("Filter repositories…")} value={filter} onChange={(e) => setFilter(e.target.value)} autoFocus />
        <ul className="divide-y divide-zinc-200 overflow-hidden glass rounded-2xl">
          {shown.map((r) => (
            <li key={r.full_name}>
              <button onClick={() => setRepo(r)} className="flex w-full items-center justify-between px-4 py-2.5 text-left text-sm hover:bg-zinc-50 dark:hover:bg-zinc-800/40">
                <span className="flex min-w-0 items-center gap-2"><span className="truncate font-medium">{r.full_name}</span><CompatBadge compat={compat[r.full_name]} /></span>
                <span className="shrink-0 text-xs text-zinc-500">{r.private ? t("private") : t("public")} · {r.default_branch}</span>
              </button>
            </li>
          ))}
          {repos && shown.length === 0 && (
            <li className="px-4 py-6 text-center text-sm text-zinc-500">
              {t("No repository. Install the GitHub App on your account or repositories (Settings → GitHub).")}
            </li>
          )}
          {!repos && !error && <li className="px-4 py-6 text-center text-sm text-zinc-500">{t("Loading repositories…")}</li>}
        </ul>
      </div>
    );
  }

  const needsStart = type === "python";
  const servesFiles = type === "static" || type === "php";

  return (
    <form onSubmit={submit} className="space-y-4">
      <button type="button" onClick={() => setRepo(null)} className="text-sm text-zinc-500 hover:underline">← {t("Repositories")}</button>
      <PageHeader eyebrow={t("New app")} title={repo.full_name} subtitle={t("Review the settings, then deploy.")} />
      {compat[repo.full_name]?.compatible && (
        <div className="glass flex items-center gap-3 rounded-xl px-4 py-3 text-sm">
          <CompatBadge compat={compat[repo.full_name]} />
          <span className="text-zinc-500">{t("This site is ready for the CMS: once deployed, give your client access from the CMS card of the app.")}</span>
        </div>
      )}
      <Card title={t("Basics")}>
        <div className="grid gap-3 text-sm sm:grid-cols-2">
          <label className="space-y-1">
            <span className="text-zinc-500">{t("App id")}</span>
            <Input value={id} onChange={(e) => setId(slug(e.target.value))} required />
            <span className="text-xs text-zinc-500">{t("Folder /srv/mas/apps/{id}, user mas-{id}", { id: id || "…" })}</span>
          </label>
          <label className="space-y-1">
            <span className="text-zinc-500">{t("Branch")}</span>
            <select value={branch} onChange={(e) => setBranch(e.target.value)} className="w-full rounded-md border border-zinc-300 bg-white px-2 py-2 dark:border-zinc-700 dark:bg-zinc-950">
              {(branches.length ? branches : [branch]).map((b) => <option key={b}>{b}</option>)}
            </select>
          </label>
          <label className="space-y-1">
            <span className="text-zinc-500">{t("Type")} {detecting && <span className="text-xs">({t("detecting…")})</span>}</span>
            <select value={type} onChange={(e) => setType(e.target.value)} required className="w-full rounded-md border border-zinc-300 bg-white px-2 py-2 dark:border-zinc-700 dark:bg-zinc-950">
              <option value="" disabled>{t("choose…")}</option>
              {DEPLOYABLE.map((type) => <option key={type}>{type}</option>)}
            </select>
          </label>
        </div>
      </Card>
      {db?.uses_supabase && (
        <Card title={t("Database")}>
          <div className="space-y-2 text-sm">
            <p className="text-zinc-500">{t("This app uses Supabase")} <span className="text-xs">({db.evidence.join(", ")})</span></p>
            {canConnect ? (
              <>
                <label className="flex items-center gap-2">
                  <input type="checkbox" checked={connectDb} onChange={(e) => setConnectDb(e.target.checked)} />
                  {db.instance === "running" ? t("Connect it to the Supabase of this server before its first build") : t("Connect it once Supabase is installed")}
                </label>
                {connectDb && db.migrations.length > 0 && <p className="text-xs text-zinc-500">{t("Its {n} migration(s) will create the tables before the build.", { n: db.migrations.length })}</p>}
                {connectDb && db.seed && <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={seedDb} onChange={(e) => setSeedDb(e.target.checked)} /> {t("Load supabase/seed.sql (sample data) once")}</label>}
              </>
            ) : (
              <p className="text-xs text-zinc-500">{t("No Supabase on this server yet: once the app is created, its Database card creates one and connects it.")}</p>
            )}
          </div>
        </Card>
      )}
      <Card title={t("Domain (optional)")} actions={
        <label className="flex items-center gap-1.5 text-xs font-normal">
          <input type="checkbox" checked={withDomain} onChange={(e) => setWithDomain(e.target.checked)} /> {t("give it an address")}
        </label>
      }>
        {withDomain ? (
          <DomainPicker suggestion={id} onChange={onDomain} />
        ) : (
          <p className="text-sm text-zinc-500">{t("No domain: the app only listens on 127.0.0.1. You can add one later.")}</p>
        )}
      </Card>
      <Card title={t("Environment variables")}>
        <textarea
          value={env}
          onChange={(e) => setEnv(e.target.value)}
          rows={5}
          placeholder={"DATABASE_URL=postgres://…\nNEXT_PUBLIC_API=https://…"}
          className="mono w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-xs dark:border-zinc-700 dark:bg-zinc-950"
        />
        <p className="mt-1 text-xs text-zinc-500">{t("Stored encrypted. Available at build time and at runtime.")}</p>
      </Card>
      <Card title={t("Build & run")}>
        <div className="grid gap-3 text-sm sm:grid-cols-2">
          <label className="space-y-1">
            <span className="text-zinc-500">{t("Build command (optional)")}</span>
            <Input value={buildCommand} onChange={(e) => setBuildCommand(e.target.value)} placeholder={t("default: npm ci && npm run build / pip install")} className="mono text-xs" />
          </label>
          <label className="space-y-1">
            <span className="text-zinc-500">{needsStart ? t("Start command (required)") : t("Start command (optional)")}</span>
            <Input value={startCommand} onChange={(e) => setStartCommand(e.target.value)} required={needsStart} placeholder={needsStart ? ".venv/bin/uvicorn main:app --host 127.0.0.1 --port $PORT" : t("default: next start / npm start")} className="mono text-xs" />
          </label>
          {servesFiles && (
            <label className="space-y-1">
              <span className="text-zinc-500">{t("Folder to serve (optional)")}</span>
              <Input value={rootDir} onChange={(e) => setRootDir(e.target.value)} placeholder="public, dist, out…" className="mono text-xs" />
            </label>
          )}
          <label className="flex items-center gap-2 self-end pb-2">
            <input type="checkbox" checked={autoDeploy} onChange={(e) => setAutoDeploy(e.target.checked)} />
            <span>{t("Deploy on every push to {branch}", { branch })}</span>
          </label>
        </div>
      </Card>
      <ErrorText>{error}</ErrorText>
      <Button variant="glow" type="submit" disabled={busy || !type} className="!h-10 px-6">{busy ? t("Creating…") : t("Deploy")}</Button>
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
