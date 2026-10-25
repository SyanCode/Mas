"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ProjectEditor } from "@/components/ProjectEditor";
import { RepoPicker } from "@/components/RepoPicker";
import { Shell } from "@/components/Shell";
import { Badge, Button, Card, CopyCode, ErrorText, PageHeader, Select } from "@/components/ui";
import { api, ApiError, type Project, type ProjectManifest, type ProjectPlan, type Repo } from "@/lib/api";
import { useT } from "@/lib/i18n";

type Tab = "file" | "compose";

const EMPTY: ProjectManifest = { name: "", id: "", env: {}, apps: [] };

/** Import a repository holding mas.project.json. */
function FromFile({ repos, onCompose }: { repos: Repo[] | null; onCompose: (repo: Repo) => void }) {
  const t = useT();
  const [repo, setRepo] = useState<Repo | null>(null);
  const [branches, setBranches] = useState<string[]>([]);
  const [branch, setBranch] = useState("");
  const [plan, setPlan] = useState<(ProjectPlan & { exists: boolean }) | null>(null);
  const [error, setError] = useState("");
  const [missingFile, setMissingFile] = useState(false);
  const [busy, setBusy] = useState(false);
  const [deploy, setDeploy] = useState(true);

  useEffect(() => {
    if (!repo) return;
    setBranch(repo.default_branch);
    api<string[]>(`/github/branches?repo=${encodeURIComponent(repo.full_name)}&installation_id=${repo.installation_id}`)
      .then(setBranches).catch(() => setBranches([repo.default_branch]));
  }, [repo]);

  useEffect(() => {
    if (!repo || !branch) return;
    setPlan(null);
    setError("");
    setMissingFile(false);
    api<ProjectPlan & { exists: boolean }>("/projects/read", { method: "POST", body: { repo: repo.full_name, branch, installation_id: repo.installation_id } })
      .then(setPlan)
      .catch((e: ApiError) => { setError(e.detail); setMissingFile(e.detail.startsWith("no mas.project.json")); });
  }, [repo, branch]);

  async function importIt() {
    if (!repo || !plan) return;
    setBusy(true);
    setError("");
    try {
      const saved = await api<Project>("/projects", {
        method: "POST", body: { manifest: plan.manifest, source: { repo: repo.full_name, branch, installation_id: repo.installation_id }, deploy },
      });
      window.location.href = `/projects/view/?id=${encodeURIComponent(saved.id)}`;
    } catch (e) {
      setError((e as ApiError).detail);
      setBusy(false);
    }
  }

  if (!repo) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-zinc-500">{t("Pick the repository that holds mas.project.json, usually the main one of the product.")}</p>
        <RepoPicker repos={repos} onSelect={setRepo} actionLabel={t("Read")} />
      </div>
    );
  }
  const problems = (plan?.apps ?? []).filter((a) => a.problem);
  return (
    <div className="space-y-4">
      <div className="glass flex flex-wrap items-center gap-3 rounded-2xl px-4 py-3">
        <span className="text-sm"><span className="text-zinc-500">{repo.owner}/</span><span className="font-semibold">{repo.full_name.split("/")[1]}</span></span>
        <Select value={branch} onChange={(e) => setBranch(e.target.value)} className="!w-auto !py-1 text-xs">
          {(branches.length ? branches : [branch]).map((b) => <option key={b}>{b}</option>)}
        </Select>
        <Button variant="ghost" className="ml-auto" onClick={() => setRepo(null)}>{t("Another repository")}</Button>
      </div>
      <ErrorText>{error}</ErrorText>
      {missingFile && (
        <Card>
          <p className="text-sm">{t("This repository has no mas.project.json yet.")}</p>
          <p className="mt-1 text-sm text-zinc-500">{t("Pick the repositories of the product and Mas writes the file for you, then commits it here if you want.")}</p>
          <Button variant="primary" className="mt-3" onClick={() => onCompose(repo)}>{t("Compose the project from this repository")}</Button>
        </Card>
      )}
      {plan && (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_24rem]">
          <Card title={<>{plan.manifest.name} {plan.exists && <Badge tone="amber">{t("already imported: updated")}</Badge>}</>}>
            <ol className="space-y-3">
              {plan.order.map((id, i) => {
                const a = plan.apps.find((x) => x.id === id)!;
                return (
                  <li key={id} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                    <span className="flex h-6 w-6 items-center justify-center rounded-full bg-zinc-200 text-xs font-medium dark:bg-white/10">{i + 1}</span>
                    <span className="font-semibold">{a.id}</span>
                    <span className="text-xs text-zinc-500">{a.repo}{a.branch ? ` · ${a.branch}` : ""}</span>
                    {a.domain && <Badge>{a.domain}</Badge>}
                    {a.depends_on.length > 0 && <span className="text-xs text-zinc-500">{t("after {names}", { names: a.depends_on.join(", ") })}</span>}
                    <span className="ml-auto">{a.problem ? <Badge tone="red">{t("conflict")}</Badge> : a.exists ? <Badge tone="emerald">{t("in Mas: joins the project")}</Badge> : <Badge tone="sky">{t("will be created")}</Badge>}</span>
                    {a.problem && <p className="w-full pl-9 text-xs text-rose-500">{a.problem}</p>}
                  </li>
                );
              })}
            </ol>
            {Object.keys(plan.manifest.env).length > 0 && (
              <p className="mt-4 text-xs text-zinc-500">{t("Shared variables: {names}", { names: Object.keys(plan.manifest.env).join(", ") })}</p>
            )}
          </Card>
          <div className="space-y-4">
            <Card title="mas.project.json"><CopyCode text={plan.file} /></Card>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={deploy} onChange={(e) => setDeploy(e.target.checked)} />
              {t("Deploy every app now, in this order")}
            </label>
            <Button variant="glow" className="!h-10 w-full" disabled={busy || problems.length > 0} onClick={importIt}>{busy ? t("Importing…") : t("Import the project")}</Button>
          </div>
        </div>
      )}
    </div>
  );
}

function NewProject() {
  const t = useT();
  const [tab, setTab] = useState<Tab>("file");
  const [repos, setRepos] = useState<Repo[] | null>(null);
  const [picked, setPicked] = useState<{ repo: string; branch: string }[]>([]);
  const [editing, setEditing] = useState<Project | null>(null);
  const [editId, setEditId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Repo[]>("/github/repos").then(setRepos).catch((e: ApiError) => setError(e.detail));
    const id = new URLSearchParams(window.location.search).get("edit");
    if (id) {
      setEditId(id);
      setTab("compose");
      api<Project>(`/projects/${encodeURIComponent(id)}`).then(setEditing).catch((e: ApiError) => setError(e.detail));
    }
  }, []);

  const initial = useMemo(() => editing?.manifest ?? EMPTY, [editing]);

  function toggle(r: Repo) {
    setPicked((list) => list.some((p) => p.repo === r.full_name) ? list.filter((p) => p.repo !== r.full_name) : [...list, { repo: r.full_name, branch: r.default_branch }]);
  }

  async function save(manifest: Record<string, unknown>, deploy: boolean) {
    setBusy(true);
    setError("");
    try {
      const saved = editId
        ? await api<Project>(`/projects/${encodeURIComponent(editId)}`, { method: "PUT", body: { manifest, deploy } })
        : await api<Project>("/projects", { method: "POST", body: { manifest, deploy } });
      window.location.href = `/projects/view/?id=${encodeURIComponent(saved.id)}`;
    } catch (e) {
      setError((e as ApiError).detail);
      setBusy(false);
    }
  }

  const selected = [...(editing?.manifest.apps.map((a) => a.repo) ?? []), ...picked.map((p) => p.repo)];

  return (
    <div className="space-y-5">
      <Link href={editId ? `/projects/view/?id=${encodeURIComponent(editId)}` : "/projects/"} className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-white">← {editId ? editId : t("Projects")}</Link>
      <PageHeader eyebrow={editId ? t("Edit project") : t("New project")} title={<>{t("Several repositories,")} <span className="text-gradient">{t("one product")}</span></>}
        subtitle={t("Each repository stays an app with its domain; the project shares variables between them and deploys them in order.")} />
      {!editId && (
        <div className="glass inline-flex rounded-xl p-1">
          {([["file", t("From a mas.project.json")], ["compose", t("Pick the repositories")]] as [Tab, string][]).map(([id, label]) => (
            <button key={id} type="button" onClick={() => setTab(id)}
              className={`rounded-lg px-3 py-1.5 text-sm transition ${tab === id ? "bg-zinc-900 text-white shadow dark:bg-white dark:text-zinc-900" : "text-zinc-500 hover:text-zinc-900 dark:hover:text-white"}`}>{label}</button>
          ))}
        </div>
      )}
      <ErrorText>{error}</ErrorText>
      {tab === "file" ? (
        <FromFile repos={repos} onCompose={(r) => { setPicked([{ repo: r.full_name, branch: r.default_branch }]); setTab("compose"); }} />
      ) : (
        <div className="space-y-5">
          <details open={!editId} className="group">
            <summary className="mb-3 cursor-pointer text-sm font-medium">{t("Repositories of the project")} <span className="text-zinc-500">({selected.length})</span></summary>
            <RepoPicker repos={repos} selected={selected} onToggle={toggle} />
          </details>
          {(!editId || editing) && (
            <ProjectEditor key={editing?.id ?? "new"} initial={initial} projectId={editId ?? undefined} extraRepos={picked} busy={busy}
              submitLabel={busy ? t("Saving…") : editId ? t("Save the project") : t("Create the project")} onSubmit={save} />
          )}
        </div>
      )}
    </div>
  );
}

export default function NewProjectPage() {
  return (
    <Shell>
      <NewProject />
    </Shell>
  );
}
