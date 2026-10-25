"use client";

/**
 * Compose a project in the dashboard: its name, shared variables and, for each repository, the app id,
 * branch, domain, the apps it waits for and its variables. The resulting mas.project.json is checked by
 * the server as it is typed and shown beside the form.
 */

import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, CopyCode, ErrorText, Input, Label, Select, Textarea } from "@/components/ui";
import { api, ApiError, SERVICE_TYPES, type ProjectApp, type ProjectManifest, type ProjectPlan } from "@/lib/api";
import { useT } from "@/lib/i18n";

export const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 24).replace(/-+$/g, "");

export function envToText(env: Record<string, string>): string {
  return Object.entries(env).map(([k, v]) => `${k}=${v}`).join("\n");
}

export function textToEnv(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const at = line.indexOf("=");
    if (at > 0) out[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return out;
}

export function emptyApp(repo: string, branch: string | null = null): ProjectApp {
  return { id: slug(repo.split("/")[1] ?? repo), repo, branch, domain: null, type: null, root_dir: "", build_command: null, start_command: null, env: {}, depends_on: [] };
}

interface Draft {
  name: string;
  env: string;
  apps: (ProjectApp & { envText: string })[];
}

function toDraft(m: ProjectManifest): Draft {
  return { name: m.name, env: envToText(m.env), apps: m.apps.map((a) => ({ ...a, envText: envToText(a.env) })) };
}

export function toManifest(d: Draft, id?: string): Record<string, unknown> {
  return {
    name: d.name, ...(id ? { id } : {}), env: textToEnv(d.env),
    apps: d.apps.map(({ envText, ...a }) => ({
      ...a, branch: a.branch || null, domain: a.domain || null, type: a.type || null,
      build_command: a.build_command || null, start_command: a.start_command || null, env: textToEnv(envText),
    })),
  };
}

interface Props {
  initial: ProjectManifest;
  /** Editing an existing project: its id stays. */
  projectId?: string;
  /** Repositories added from the picker above. */
  extraRepos?: { repo: string; branch: string }[];
  busy?: boolean;
  submitLabel: string;
  onSubmit: (manifest: Record<string, unknown>, deploy: boolean) => void;
}

export function ProjectEditor({ initial, projectId, extraRepos = [], busy, submitLabel, onSubmit }: Props) {
  const t = useT();
  const [draft, setDraft] = useState<Draft>(() => toDraft(initial));
  const [plan, setPlan] = useState<ProjectPlan | null>(null);
  const [error, setError] = useState("");
  const [deploy, setDeploy] = useState(true);

  // Repositories picked above join the project once.
  useEffect(() => {
    setDraft((d) => {
      const have = new Set(d.apps.map((a) => a.repo.toLowerCase()));
      const added = extraRepos.filter((r) => !have.has(r.repo.toLowerCase())).map((r) => ({ ...emptyApp(r.repo, r.branch), envText: "" }));
      const keep = d.apps.filter((a) => extraRepos.some((r) => r.repo.toLowerCase() === a.repo.toLowerCase()) || initial.apps.some((i) => i.repo.toLowerCase() === a.repo.toLowerCase()));
      return added.length || keep.length !== d.apps.length ? { ...d, apps: [...keep, ...added] } : d;
    });
  }, [extraRepos, initial.apps]);

  const manifest = useMemo(() => toManifest(draft, projectId), [draft, projectId]);

  useEffect(() => {
    if (!draft.name.trim() || !draft.apps.length) {
      setPlan(null);
      setError("");
      return;
    }
    const timer = setTimeout(() => {
      api<ProjectPlan>("/projects/validate", { method: "POST", body: { manifest } })
        .then((p) => { setPlan(p); setError(""); })
        .catch((e: ApiError) => { setPlan(null); setError(e.detail); });
    }, 350);
    return () => clearTimeout(timer);
  }, [manifest, draft.name, draft.apps.length]);

  const setApp = (i: number, patch: Partial<Draft["apps"][number]>) =>
    setDraft((d) => ({ ...d, apps: d.apps.map((a, j) => (j === i ? { ...a, ...patch } : a)) }));
  const ids = draft.apps.map((a) => a.id);
  const problems = (plan?.apps ?? []).filter((a) => a.problem);

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_24rem]">
      <div className="space-y-4">
        <Card title={t("Project")}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Label title={t("Name")} hint={projectId ? t("Id: {id}", { id: projectId }) : t("Id: {id}", { id: slug(draft.name) || "…" })}>
              <Input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="Klarfi" maxLength={60} />
            </Label>
            <Label title={t("Variables shared by every app")} hint={t("Not secret: they go in the repository file. Secrets stay in the variables of each app.")}>
              <Textarea rows={3} value={draft.env} onChange={(e) => setDraft({ ...draft, env: e.target.value })} placeholder="NEXT_PUBLIC_BRAND=Klarfi" />
            </Label>
          </div>
        </Card>
        {draft.apps.map((a, i) => {
          const status = plan?.apps.find((p) => p.id === a.id);
          return (
            <Card key={`${a.repo}-${i}`} title={<span className="flex items-center gap-2"><span className="text-zinc-500">{i + 1}.</span> {a.repo}</span>} actions={
              <>
                {status?.exists ? <Badge tone="emerald">{t("in Mas")}</Badge> : <Badge tone="sky">{t("will be created")}</Badge>}
                <Button variant="ghost" onClick={() => setDraft((d) => ({ ...d, apps: d.apps.filter((_, j) => j !== i), }))}>{t("Remove")}</Button>
              </>
            }>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                <Label title={t("App id")}><Input value={a.id} onChange={(e) => setApp(i, { id: slug(e.target.value) })} /></Label>
                <Label title={t("Branch")} hint={t("Empty: the default branch.")}><Input value={a.branch ?? ""} onChange={(e) => setApp(i, { branch: e.target.value || null })} placeholder="main" /></Label>
                <Label title={t("Domain")}><Input value={a.domain ?? ""} onChange={(e) => setApp(i, { domain: e.target.value.toLowerCase() || null })} placeholder="app.example.com" /></Label>
                <Label title={t("Type")}>
                  <Select value={a.type ?? ""} onChange={(e) => setApp(i, { type: e.target.value || null })}>
                    <option value="">{t("detected")}</option>
                    {SERVICE_TYPES.filter((x) => x !== "unknown").map((x) => <option key={x}>{x}</option>)}
                  </Select>
                </Label>
                <div className="space-y-1.5 text-sm sm:col-span-2">
                  <span className="text-[13px] font-medium text-zinc-700 dark:text-zinc-300">{t("Deployed after")}</span>
                  <div className="flex flex-wrap gap-2">
                    {ids.filter((other) => other !== a.id).map((other) => (
                      <label key={other} className={`flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${a.depends_on.includes(other) ? "border-indigo-400 bg-indigo-500/10" : "border-zinc-300 dark:border-white/10"}`}>
                        <input type="checkbox" className="hidden" checked={a.depends_on.includes(other)}
                          onChange={(e) => setApp(i, { depends_on: e.target.checked ? [...a.depends_on, other] : a.depends_on.filter((d) => d !== other) })} />
                        {other}
                      </label>
                    ))}
                    {ids.length < 2 && <span className="text-xs text-zinc-500">{t("Add another repository to link them.")}</span>}
                  </div>
                </div>
                <div className="sm:col-span-2 lg:col-span-3">
                  <Label title={t("Variables of this app")} hint={<>{t("Name another app of the project:")} {ids.filter((o) => o !== a.id).slice(0, 1).map((o) => <code key={o} className="mx-1 text-[11px]">{`\${apps.${o}.url}`}</code>)} · <code className="text-[11px]">.domain</code> · <code className="text-[11px]">.internal_url</code> · <code className="text-[11px]">.port</code></>}>
                    <Textarea rows={3} value={a.envText} onChange={(e) => setApp(i, { envText: e.target.value })}
                      placeholder={ids.find((o) => o !== a.id) ? `NEXT_PUBLIC_APP_URL=\${apps.${ids.find((o) => o !== a.id)}.url}` : "API_URL=https://…"} />
                  </Label>
                </div>
              </div>
            </Card>
          );
        })}
        {draft.apps.length === 0 && <p className="glass rounded-2xl px-5 py-8 text-center text-sm text-zinc-500">{t("Pick the repositories of the project above.")}</p>}
      </div>
      <div className="space-y-4 xl:sticky xl:top-20 xl:self-start">
        <Card title={t("Deploy order")}>
          {plan ? (
            <ol className="space-y-2 text-sm">
              {plan.order.map((id, i) => {
                const entry = plan.apps.find((a) => a.id === id);
                return (
                  <li key={id} className="flex items-center gap-2">
                    <span className="flex h-5 w-5 items-center justify-center rounded-full bg-zinc-200 text-[11px] font-medium dark:bg-white/10">{i + 1}</span>
                    <span className="font-medium">{id}</span>
                    {entry?.domain && <span className="truncate text-xs text-zinc-500">{entry.domain}</span>}
                    {entry?.depends_on.length ? <span className="ml-auto text-[11px] text-zinc-500">{t("after {names}", { names: entry.depends_on.join(", ") })}</span> : null}
                  </li>
                );
              })}
            </ol>
          ) : <p className="text-sm text-zinc-500">{t("Checked as you type.")}</p>}
        </Card>
        <ErrorText>{error}</ErrorText>
        {problems.map((p) => <ErrorText key={p.id}>{p.problem}</ErrorText>)}
        {plan && (
          <Card title="mas.project.json">
            <CopyCode text={plan.file} />
            <p className="mt-2 text-xs text-zinc-500">{t("Once created, the project can write this file to one of its repositories: a push changing it then updates the project.")}</p>
          </Card>
        )}
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={deploy} onChange={(e) => setDeploy(e.target.checked)} />
          {t("Deploy every app now, in this order")}
        </label>
        <Button variant="glow" className="!h-10 w-full" disabled={busy || !plan || problems.length > 0} onClick={() => onSubmit(manifest, deploy)}>{submitLabel}</Button>
      </div>
    </div>
  );
}
