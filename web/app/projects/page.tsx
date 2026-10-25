"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { Badge, EmptyState, ErrorText, Monogram, PageHeader, ReleaseBadge, StatusDot } from "@/components/ui";
import { api, ApiError, timeAgo, type Project } from "@/lib/api";
import { useT } from "@/lib/i18n";

function ProjectCard({ p }: { p: Project }) {
  const t = useT();
  const apps = p.order.map((id) => p.apps.find((a) => a.id === id)!).filter(Boolean);
  const missing = apps.filter((a) => !a.exists).length;
  const frozen = apps.filter((a) => a.app && !a.app.auto_deploy).length;
  return (
    <Link href={`/projects/view/?id=${encodeURIComponent(p.id)}`}
      className="glass group flex flex-col gap-4 rounded-2xl p-5 transition duration-300 hover:-translate-y-0.5 hover:border-indigo-400/40">
      <div className="flex items-center gap-3">
        <Monogram name={p.name} />
        <div className="min-w-0 flex-1">
          <h3 className="truncate font-semibold tracking-tight">{p.name}</h3>
          <p className="truncate text-xs text-zinc-500">{p.source ? `${p.source.repo} · ${p.source.branch}` : t("composed in Mas")}</p>
        </div>
        <Badge tone="violet">{t("{n} app(s)", { n: apps.length })}</Badge>
      </div>
      <ol className="space-y-1.5">
        {apps.map((a, i) => (
          <li key={a.id} className="flex items-center gap-2 text-sm">
            <span className="w-4 text-right text-xs text-zinc-500">{i + 1}</span>
            <StatusDot status={a.app?.last_release?.status ?? "unknown"} size="sm" />
            <span className="truncate font-medium">{a.id}</span>
            <span className="truncate text-xs text-zinc-500">{a.domain ?? ""}</span>
            <span className="ml-auto shrink-0">{a.app?.last_release ? <ReleaseBadge status={a.app.last_release.status} /> : <Badge>{a.exists ? t("Never deployed") : t("to create")}</Badge>}</span>
          </li>
        ))}
      </ol>
      <div className="mt-auto flex flex-wrap items-center gap-2 border-t border-zinc-200/70 pt-3 text-xs text-zinc-500 dark:border-white/[0.06]">
        {missing > 0 && <Badge tone="amber">{t("{n} app(s) to create", { n: missing })}</Badge>}
        {frozen > 0 && <Badge tone="sky">❄ {t("{n} frozen", { n: frozen })}</Badge>}
        <span className="ml-auto">{t("updated {when}", { when: timeAgo(p.updated_at) })}</span>
      </div>
    </Link>
  );
}

function Projects() {
  const t = useT();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<Project[]>("/projects").then(setProjects).catch((e: ApiError) => setError(e.detail));
  }, []);
  const newButton = (
    <Link href="/projects/new/" className="mas-cta inline-flex h-9 items-center gap-2 rounded-lg px-4 text-sm font-medium text-white transition hover:brightness-110">
      <span className="text-base leading-none">+</span> {t("New project")}
    </Link>
  );
  return (
    <>
      <Link href="/apps/" className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-white">← {t("Apps")}</Link>
      <PageHeader eyebrow={t("Deployments")} title={t("Projects")} subtitle={t("Several repositories deployed as one product: a landing page and its dashboard, a front and its API.")} actions={newButton} />
      <ErrorText>{error}</ErrorText>
      {projects && projects.length === 0 && (
        <EmptyState title={t("No project yet")} action={newButton}>
          {t("Import a repository holding a mas.project.json, or pick the repositories of a product and Mas writes the file for you.")}
        </EmptyState>
      )}
      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
        {(projects ?? []).map((p) => <ProjectCard key={p.id} p={p} />)}
        {!projects && !error && Array.from({ length: 2 }).map((_, i) => <div key={i} className="glass shimmer h-56 rounded-2xl" />)}
      </div>
    </>
  );
}

export default function ProjectsPage() {
  return (
    <Shell>
      <Projects />
    </Shell>
  );
}
