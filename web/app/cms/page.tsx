"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Shell, useSession } from "@/components/Shell";
import { EmptyState, ErrorText, Monogram, PageHeader, ReleaseBadge } from "@/components/ui";
import { api, ApiError, timeAgo, type CmsSite } from "@/lib/api";

function Sites() {
  const session = useSession();
  const [sites, setSites] = useState<CmsSite[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<CmsSite[]>("/cms/sites").then(setSites).catch((e: ApiError) => setError(e.detail));
  }, []);

  return (
    <>
      <PageHeader eyebrow="Content" title={<>Your <span className="text-gradient">sites</span></>}
        subtitle="Change the texts and images of your website. Every change is published automatically." />
      <ErrorText>{error}</ErrorText>
      {sites === null && !error && <div className="glass shimmer h-32 rounded-2xl" />}
      {sites?.length === 0 && (
        <EmptyState title="No site yet">
          {session?.role === "admin" ? "Link an app to GitHub first." : "Your administrator has not given you access to a site yet."}
        </EmptyState>
      )}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {sites?.map((s) => (
          <Link key={s.id} href={`/cms/edit/?app=${encodeURIComponent(s.id)}`}
            className="glass ring-gradient group block animate-fade-up rounded-2xl p-5 transition hover:-translate-y-0.5">
            <div className="flex items-center gap-3">
              <Monogram name={s.name} />
              <div className="min-w-0">
                <div className="truncate font-semibold">{s.name}</div>
                <div className="truncate text-xs text-zinc-500">{s.domain ?? "no domain yet"}</div>
              </div>
            </div>
            <div className="mt-4 flex items-center justify-between text-xs text-zinc-500">
              {s.last_release ? <span className="flex items-center gap-2"><ReleaseBadge status={s.last_release.status} /> {timeAgo(s.last_release.created_at)}</span> : <span>Not published yet</span>}
              <span className="font-medium text-indigo-500 group-hover:underline">Edit →</span>
            </div>
          </Link>
        ))}
      </div>
    </>
  );
}

export default function CmsPage() {
  return (
    <Shell>
      <Sites />
    </Shell>
  );
}
