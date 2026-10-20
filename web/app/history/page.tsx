"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { ErrorText } from "@/components/ui";
import { api, ApiError, type HistoryEntry } from "@/lib/api";

function History() {
  const [rows, setRows] = useState<HistoryEntry[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<HistoryEntry[]>("/history?limit=200").then(setRows).catch((e: ApiError) => setError(e.detail));
  }, []);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">History</h1>
      <ErrorText>{error}</ErrorText>
      <div className="overflow-x-auto rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-zinc-200 text-xs uppercase text-zinc-500 dark:border-zinc-800">
            <tr>
              <th className="px-4 py-2 font-medium">When</th>
              <th className="px-4 py-2 font-medium">Who</th>
              <th className="px-4 py-2 font-medium">Action</th>
              <th className="px-4 py-2 font-medium">Service</th>
              <th className="px-4 py-2 font-medium">Result</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {(rows ?? []).map((r) => (
              <tr key={r.id} className="align-top">
                <td className="whitespace-nowrap px-4 py-2 text-zinc-500">{r.at.replace("T", " ").slice(0, 19)}</td>
                <td className="px-4 py-2">{r.actor}</td>
                <td className="px-4 py-2">{r.action}</td>
                <td className="px-4 py-2">
                  {r.service_id ? <Link href={`/service/?id=${encodeURIComponent(r.service_id)}`} className="hover:underline">{r.service_id}</Link> : <span className="text-zinc-400">-</span>}
                </td>
                <td className="px-4 py-2">
                  <span className={r.ok ? "text-emerald-600" : "text-red-600"}>{r.ok ? "ok" : "failed"}</span>
                  {r.detail && <div className="mono max-w-md truncate text-xs text-zinc-500" title={r.detail}>{r.detail}</div>}
                </td>
              </tr>
            ))}
            {rows && rows.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-zinc-500">Nothing yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function HistoryPage() {
  return (
    <Shell>
      <History />
    </Shell>
  );
}
