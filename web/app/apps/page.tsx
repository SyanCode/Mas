"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { Badge, ErrorText, ReleaseBadge } from "@/components/ui";
import { api, ApiError, timeAgo, type App, type GitHubStatus } from "@/lib/api";

function Apps() {
  const [apps, setApps] = useState<App[] | null>(null);
  const [github, setGithub] = useState<GitHubStatus | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const load = () => api<App[]>("/apps").then(setApps).catch((e: ApiError) => setError(e.detail));
    load();
    api<GitHubStatus>("/github/status").then(setGithub).catch(() => {});
    const t = setInterval(load, 10_000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Apps</h1>
          <p className="text-sm text-zinc-500">Deployed from GitHub on every push.</p>
        </div>
        {github?.configured && (
          <Link href="/apps/new/" className="rounded-md bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900">
            New app
          </Link>
        )}
      </div>
      <ErrorText>{error}</ErrorText>

      {github && !github.configured && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
          Connect GitHub first: <Link href="/settings/" className="font-medium underline">Settings → GitHub</Link>.
        </div>
      )}

      <ul className="divide-y divide-zinc-200 overflow-hidden rounded-lg border border-zinc-200 bg-white dark:divide-zinc-800 dark:border-zinc-800 dark:bg-zinc-900">
        {(apps ?? []).map((a) => (
          <li key={a.id} className="relative flex flex-col gap-2 px-4 py-3 hover:bg-zinc-50 sm:flex-row sm:items-center dark:hover:bg-zinc-800/40">
            <div className="min-w-0 flex-1">
              <Link href={`/apps/view/?id=${encodeURIComponent(a.id)}`} className="font-medium after:absolute after:inset-0 hover:underline">
                {a.id}
              </Link>
              <div className="text-xs text-zinc-500">
                {a.repo}@{a.branch}
                {a.domain && <> · <a href={`https://${a.domain}`} target="_blank" rel="noreferrer" className="relative z-[1] hover:underline">{a.domain} ↗</a></>}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <Badge tone="sky">{a.type}</Badge>
              {a.mode === "adopted" && <Badge>adopted</Badge>}
              {!a.auto_deploy && <Badge>manual</Badge>}
              {a.last_release && <ReleaseBadge status={a.last_release.status} />}
              {a.current && <span className="mono text-zinc-500">{(a.current.sha ?? "").slice(0, 7)} · {timeAgo(a.current.created_at)}</span>}
            </div>
          </li>
        ))}
        {apps && apps.length === 0 && (
          <li className="px-4 py-8 text-center text-sm text-zinc-500">
            No app yet. Create one from a GitHub repository, or open a detected service with a GitHub remote and
            click “Connect to GitHub”.
          </li>
        )}
      </ul>
    </div>
  );
}

export default function AppsPage() {
  return (
    <Shell>
      <Apps />
    </Shell>
  );
}
