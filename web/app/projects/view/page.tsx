"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { RepoPicker } from "@/components/RepoPicker";
import { Shell } from "@/components/Shell";
import { Badge, Button, Card, ConfirmDialog, CopyCode, ErrorText, Monogram, Notice, ReleaseBadge, Select, StatusDot } from "@/components/ui";
import { api, ApiError, timeAgo, type Project, type Repo } from "@/lib/api";
import { useT } from "@/lib/i18n";

/** Commit mas.project.json to one of the repositories (the project then follows that file). */
function ExportCard({ project, onDone }: { project: Project; onDone: (p: Project) => void }) {
  const t = useT();
  const [repos, setRepos] = useState<Repo[] | null>(null);
  const [repo, setRepo] = useState<Repo | null>(null);
  const [branch, setBranch] = useState("");
  const [branches, setBranches] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open || repos) return;
    api<Repo[]>("/github/repos").then((all) => {
      const own = new Set(project.manifest.apps.map((a) => a.repo.toLowerCase()));
      setRepos(all.filter((r) => own.has(r.full_name.toLowerCase())));  // one of the project's repositories
    }).catch((e: ApiError) => setError(e.detail));
  }, [open, repos, project]);

  useEffect(() => {
    if (!repo) return;
    setBranch(repo.default_branch);
    api<string[]>(`/github/branches?repo=${encodeURIComponent(repo.full_name)}&installation_id=${repo.installation_id}`).then(setBranches).catch(() => setBranches([repo.default_branch]));
  }, [repo]);

  async function run() {
    if (!repo) return;
    setBusy(true);
    setError("");
    try {
      onDone(await api<Project>(`/projects/${encodeURIComponent(project.id)}/export`, { method: "POST", body: { repo: repo.full_name, branch, installation_id: repo.installation_id } }));
      setOpen(false);
      setRepo(null);
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="mas.project.json" actions={<Button onClick={() => setOpen((o) => !o)}>{project.source ? t("Write to another repository") : t("Write to a repository")}</Button>}>
      <CopyCode text={project.file} />
      <p className="mt-2 text-xs text-zinc-500">
        {project.source ? t("Followed in {repo} on {branch}: a push changing the file updates this project.", { repo: project.source.repo, branch: project.source.branch }) : t("Composed in Mas. Write it to one of the repositories to keep it with the code.")}
      </p>
      {open && (
        <div className="mt-4 space-y-3">
          <ErrorText>{error}</ErrorText>
          {!repo ? <RepoPicker repos={repos} onSelect={setRepo} actionLabel={t("Choose")} /> : (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium">{repo.full_name}</span>
              <Select value={branch} onChange={(e) => setBranch(e.target.value)} className="!w-auto !py-1 text-xs">
                {(branches.length ? branches : [branch]).map((b) => <option key={b}>{b}</option>)}
              </Select>
              <Button variant="primary" onClick={run} disabled={busy}>{busy ? t("Committing…") : t("Commit the file")}</Button>
              <Button variant="ghost" onClick={() => setRepo(null)}>{t("Cancel")}</Button>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function ProjectView() {
  const t = useT();
  const params = useSearchParams();
  const id = params.get("id") ?? "";
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState("");

  const load = useCallback(() => {
    api<Project>(`/projects/${encodeURIComponent(id)}`).then(setProject).catch((e: ApiError) => setError(e.detail));
  }, [id]);
  useEffect(() => {
    if (!id) return;
    load();
    const timer = setInterval(load, 8_000);
    return () => clearInterval(timer);
  }, [id, load]);

  async function act(name: string, fn: () => Promise<string>) {
    setBusy(name);
    setError("");
    setNotice("");
    try {
      setNotice(await fn());
      load();
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy("");
    }
  }

  if (error && !project) return <ErrorText>{error}</ErrorText>;
  if (!project) return <div className="glass shimmer h-48 rounded-2xl" />;

  const apps = project.order.map((appId) => project.apps.find((a) => a.id === appId)!).filter(Boolean);
  const existing = apps.filter((a) => a.app).map((a) => a.id);
  const allFrozen = apps.every((a) => !a.app || !a.app.auto_deploy);
  const missing = apps.filter((a) => !a.exists).length;

  return (
    <div className="space-y-6">
      <Link href="/projects/" className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-white">← {t("Projects")}</Link>
      <div className="flex flex-wrap items-center justify-between gap-4 animate-fade-up">
        <div className="flex min-w-0 items-center gap-4">
          <Monogram name={project.name} size="lg" />
          <div className="min-w-0">
            <h1 className="truncate text-[28px] font-semibold tracking-[-0.02em]">{project.name}</h1>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-zinc-500">
              <Badge tone="violet">{t("{n} app(s)", { n: apps.length })}</Badge>
              <span>{project.source ? `${project.source.repo} · ${project.source.branch}` : t("composed in Mas")}</span>
              <span>· {t("updated {when}", { when: timeAgo(project.updated_at) })}</span>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {project.source && <Button disabled={!!busy} onClick={() => act("sync", async () => {
            const r = await api<Project & { created: string[] }>(`/projects/${encodeURIComponent(id)}/sync`, { method: "POST" });
            return r.created.length ? t("File read again: {names} created and deploying.", { names: r.created.join(", ") }) : t("File read again: the project is up to date.");
          })}>{t("Read the file again")}</Button>}
          <Link href={`/projects/new/?edit=${encodeURIComponent(id)}`} className="glass inline-flex h-8 items-center rounded-lg px-3 text-[13px] font-medium hover:bg-white/[0.08]">{t("Edit")}</Link>
          <Button disabled={!!busy || !existing.length} onClick={() => act("freeze", async () => {
            await api("/apps/freeze", { method: "POST", body: { app_ids: existing, frozen: !allFrozen } });
            return allFrozen ? t("The apps of the project deploy on push again.") : t("The apps of the project are frozen: pushes wait for you.");
          })}>{allFrozen ? t("Unfreeze") : t("❄ Freeze")}</Button>
          <Button variant="glow" disabled={!!busy || !existing.length} onClick={() => act("deploy", async () => {
            const r = await api<{ queued: { app_id: string }[] }>(`/projects/${encodeURIComponent(id)}/deploy`, { method: "POST", body: { include_frozen: true } });
            return t("{n} deployment(s) queued, in order: {names}.", { n: r.queued.length, names: r.queued.map((q) => q.app_id).join(" → ") });
          })}>{busy === "deploy" ? t("Deploying…") : t("Deploy the project")}</Button>
          <Button variant="ghost" onClick={() => setConfirmDelete(true)}>{t("Remove")}</Button>
        </div>
      </div>

      <ErrorText>{error}</ErrorText>
      <Notice>{notice}</Notice>
      {missing > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-amber-400/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-800 dark:text-amber-200">
          <span className="flex-1">{t("{n} app(s) of the file are not in Mas yet.", { n: missing })}</span>
          <Button disabled={!!busy} onClick={() => act("create", async () => {
            const r = await api<Project & { created: string[] }>(`/projects/${encodeURIComponent(id)}`, { method: "PUT", body: { manifest: project.manifest, deploy: false } });
            return t("{names} created and deploying.", { names: r.created.join(", ") || "—" });
          })}>{t("Create them")}</Button>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_26rem]">
        <Card title={t("Apps, in deploy order")} padded={false}>
          <ol>
            {apps.map((a, i) => {
              const last = a.app?.last_release;
              return (
                <li key={a.id} className="relative flex items-start gap-4 border-b border-zinc-200/70 px-5 py-4 last:border-0 dark:border-white/[0.06]">
                  <div className="flex flex-col items-center">
                    <span className="flex h-7 w-7 items-center justify-center rounded-full bg-zinc-200 text-xs font-semibold dark:bg-white/10">{i + 1}</span>
                    {i < apps.length - 1 && <span className="mt-1 h-full min-h-6 w-px bg-zinc-200 dark:bg-white/10" />}
                  </div>
                  <div className="min-w-0 flex-1 space-y-1.5">
                    <div className="flex flex-wrap items-center gap-2">
                      {a.app ? <Link href={`/apps/view/?id=${encodeURIComponent(a.id)}`} className="font-semibold hover:underline">{a.id}</Link> : <span className="font-semibold">{a.id}</span>}
                      {last ? <ReleaseBadge status={last.status} /> : <Badge>{a.exists ? t("Never deployed") : t("to create")}</Badge>}
                      {a.app && !a.app.auto_deploy && <Badge tone="sky">❄ {t("frozen")}</Badge>}
                      {a.app?.pending && <Badge tone="amber">{t("{n} commit(s) waiting", { n: a.app.pending.commits })}</Badge>}
                      {a.problem && <Badge tone="red">{t("conflict")}</Badge>}
                    </div>
                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-500">
                      <span>{a.repo}{a.app ? ` · ${a.app.branch}` : a.branch ? ` · ${a.branch}` : ""}</span>
                      {(a.app?.domain ?? a.domain) && <a href={`https://${a.app?.domain ?? a.domain}`} target="_blank" rel="noreferrer" className="hover:underline">{a.app?.domain ?? a.domain} ↗</a>}
                      {a.depends_on.length > 0 && <span>{t("after {names}", { names: a.depends_on.join(", ") })}</span>}
                      {last && <span className="flex items-center gap-1"><StatusDot status={last.status} size="sm" />{last.message || last.trigger} · {timeAgo(last.created_at)}</span>}
                    </div>
                    {Object.keys(a.env).length > 0 && (
                      <div className="flex flex-wrap gap-1.5 pt-1">
                        {Object.entries(a.env).map(([k, v]) => <code key={k} title={v} className="rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] dark:bg-white/[0.06]">{k}</code>)}
                      </div>
                    )}
                    {a.problem && <p className="text-xs text-rose-500">{a.problem}</p>}
                  </div>
                </li>
              );
            })}
          </ol>
        </Card>
        <div className="space-y-6">
          <Card title={t("Shared variables")}>
            {Object.keys(project.manifest.env).length ? (
              <ul className="space-y-1 text-xs">
                {Object.entries(project.manifest.env).map(([k, v]) => <li key={k} className="mono truncate"><span className="font-medium">{k}</span>=<span className="text-zinc-500">{v}</span></li>)}
              </ul>
            ) : <p className="text-sm text-zinc-500">{t("None. Secrets stay in the variables of each app.")}</p>}
          </Card>
          <ExportCard project={project} onDone={(p) => { setProject(p); setNotice(t("mas.project.json committed to {repo}.", { repo: p.source?.repo ?? "" })); }} />
        </div>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title={t("Remove the project {name}?", { name: project.name })}
        message={t("Only the link between the apps goes away: every app stays deployed as it is, and mas.project.json stays in its repository.")}
        confirmLabel={t("Remove")}
        danger
        onConfirm={async () => {
          setConfirmDelete(false);
          try {
            await api(`/projects/${encodeURIComponent(id)}`, { method: "DELETE" });
            window.location.href = "/projects/";
          } catch (e) {
            setError((e as ApiError).detail);
          }
        }}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}

export default function ProjectViewPage() {
  return (
    <Shell>
      <Suspense fallback={<div className="glass shimmer h-48 rounded-2xl" />}>
        <ProjectView />
      </Suspense>
    </Shell>
  );
}
