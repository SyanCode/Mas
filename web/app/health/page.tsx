"use client";

import { useCallback, useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { Badge, Button, CopyCode, ErrorText } from "@/components/ui";
import { api, ApiError, timeAgo, type Check } from "@/lib/api";

const TONE = { error: "red", warning: "amber", info: "sky" } as const;

function Health() {
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [checkedAt, setCheckedAt] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (refresh: boolean) => {
    setBusy(true);
    try {
      const r = await api<{ checked_at: number; checks: Check[] }>(`/health/checks${refresh ? "?refresh=1" : ""}`);
      setChecks(r.checks);
      setCheckedAt(r.checked_at);
      setError("");
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { load(false); }, [load]);

  const count = (level: Check["level"]) => (checks ?? []).filter((c) => c.level === level).length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Health</h1>
          <p className="text-sm text-zinc-500">
            Certificates, certbot renewal, nginx and DNS.{" "}
            {checkedAt ? `Checked ${timeAgo(new Date(checkedAt * 1000).toISOString())}.` : ""}
          </p>
        </div>
        <Button variant="primary" onClick={() => load(true)} disabled={busy}>{busy ? "Checking…" : "Check again"}</Button>
      </div>
      <ErrorText>{error}</ErrorText>
      {checks && (
        <div className="flex gap-3 text-sm">
          <Badge tone="red">{count("error")} error(s)</Badge>
          <Badge tone="amber">{count("warning")} warning(s)</Badge>
          <Badge tone="sky">{count("info")} info</Badge>
        </div>
      )}
      {checks && checks.length === 0 && (
        <p className="rounded-lg border border-emerald-300 bg-emerald-50 p-4 text-sm text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300">
          ✓ No problem found.
        </p>
      )}
      <ul className="space-y-3">
        {(checks ?? []).map((c) => (
          <li key={`${c.category}-${c.title}`} className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={TONE[c.level]}>{c.level}</Badge>
              <span className="font-medium">{c.title}</span>
              <span className="text-xs text-zinc-500">{c.category}</span>
            </div>
            {c.detail && <p className="mt-1.5 whitespace-pre-wrap text-sm text-zinc-600 dark:text-zinc-400">{c.detail}</p>}
            {c.fix && (
              <div className="mt-2 space-y-1">
                <p className="text-xs text-zinc-500">Suggested fix (run on the server):</p>
                <CopyCode text={c.fix} />
              </div>
            )}
          </li>
        ))}
      </ul>
      {!checks && !error && <p className="text-sm text-zinc-500">Running checks (DNS lookups can take a few seconds)…</p>}
    </div>
  );
}

export default function HealthPage() {
  return (
    <Shell>
      <Health />
    </Shell>
  );
}
