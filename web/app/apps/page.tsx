"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { Badge, EmptyState, ErrorText, Monogram, PageHeader, ReleaseBadge, StatusDot } from "@/components/ui";
import { api, ApiError, timeAgo, type App, type GitHubStatus } from "@/lib/api";
import { useT } from "@/lib/i18n";

function hue(name: string) {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return h % 360;
}

function AppCard({ a }: { a: App }) {
  const t = useT();
  const last = a.last_release;
  const h = hue(a.id);
  return (
    <Link
      href={`/apps/view/?id=${encodeURIComponent(a.id)}`}
      className="glass group flex flex-col overflow-hidden rounded-2xl transition duration-300 hover:-translate-y-0.5 hover:border-indigo-400/40 hover:shadow-[0_24px_70px_-30px_rgba(99,102,241,0.7)]"
    >
      <div
        className="relative h-28 overflow-hidden border-b border-white/[0.06]"
        style={{ background: `radial-gradient(120% 120% at 0% 0%, hsl(${h} 90% 60% / .55), transparent 55%), radial-gradient(120% 120% at 100% 100%, hsl(${(h + 70) % 360} 90% 55% / .45), transparent 55%), #0a0b10` }}
      >
        <div className="absolute inset-0 opacity-30 [background-image:linear-gradient(rgba(255,255,255,.08)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,.08)_1px,transparent_1px)] [background-size:22px_22px]" />
        <div className="absolute bottom-3 left-4 flex items-center gap-3">
          <Monogram name={a.id} />
          <span className="text-sm font-medium text-white/90 drop-shadow">{a.domain ?? t("no domain yet")}</span>
        </div>
        {last && <div className="absolute right-3 top-3"><ReleaseBadge status={last.status} /></div>}
      </div>
      <div className="flex flex-1 flex-col gap-3 p-5">
        <div className="flex items-center justify-between gap-2">
          <h3 className="truncate font-semibold tracking-tight">{a.id}</h3>
          <div className="flex gap-1.5">
            <Badge tone="sky">{a.type}</Badge>
            {a.mode === "adopted" && <Badge tone="violet">{t("adopted")}</Badge>}
          </div>
        </div>
        <p className="truncate text-xs text-zinc-500">
          <svg viewBox="0 0 16 16" className="mr-1 inline h-3 w-3 align-[-2px]" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" /></svg>
          {a.repo} · {a.branch}
        </p>
        <div className="mt-auto flex items-center gap-2 border-t border-zinc-200/70 pt-3 text-xs text-zinc-500 dark:border-white/[0.06]">
          {last ? (
            <>
              <StatusDot status={last.status} size="sm" />
              <span className="truncate">{last.message || last.trigger}</span>
              <span className="ml-auto shrink-0">{timeAgo(last.created_at)}</span>
            </>
          ) : (
            <span>{t("Never deployed")}</span>
          )}
        </div>
      </div>
    </Link>
  );
}

function Apps() {
  const t = useT();
  const [apps, setApps] = useState<App[] | null>(null);
  const [github, setGithub] = useState<GitHubStatus | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const load = () => api<App[]>("/apps").then(setApps).catch((e: ApiError) => setError(e.detail));
    load();
    api<GitHubStatus>("/github/status").then(setGithub).catch(() => {});
    const timer = setInterval(load, 8_000);
    return () => clearInterval(timer);
  }, []);

  const newButton = github?.configured ? (
    <Link href="/apps/new/" className="inline-flex h-9 items-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 via-sky-500 to-fuchsia-500 px-4 text-sm font-medium text-white shadow-[0_0_30px_-8px_rgba(99,102,241,0.9)] transition hover:brightness-110">
      <span className="text-base leading-none">+</span> {t("New app")}
    </Link>
  ) : null;

  return (
    <>
      <PageHeader eyebrow={t("Deployments")} title={t("Apps")} subtitle={t("Push to GitHub, Mas builds and ships it. Atomic releases, instant rollback.")} actions={newButton} />
      <ErrorText>{error}</ErrorText>
      {github && !github.configured && (
        <EmptyState title={t("Connect GitHub to start deploying")} action={<Link href="/settings/" className="text-sm font-medium text-indigo-500 hover:underline">{t("Settings")} → GitHub →</Link>}>
          {t("One click creates a private GitHub App on your account; then pick a repository and Mas deploys every push.")}
        </EmptyState>
      )}
      {apps && apps.length === 0 && github?.configured && (
        <EmptyState title={t("No app yet")} action={newButton}>
          {t("Deploy a repository, or open a detected service with a GitHub remote and click “Connect to GitHub”.")}
        </EmptyState>
      )}
      <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
        {(apps ?? []).map((a) => <AppCard key={a.id} a={a} />)}
        {!apps && !error && Array.from({ length: 3 }).map((_, i) => <div key={i} className="glass shimmer h-64 rounded-2xl" />)}
      </div>
    </>
  );
}

export default function AppsPage() {
  return (
    <Shell>
      <Apps />
    </Shell>
  );
}
