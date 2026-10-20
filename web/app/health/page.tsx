"use client";

import { useCallback, useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { Badge, Button, CopyCode, ErrorText, PageHeader, Spinner } from "@/components/ui";
import { api, ApiError, timeAgo, type Check } from "@/lib/api";
import { useT } from "@/lib/i18n";

const TONE = { error: "red", warning: "amber", info: "sky" } as const;

function Health() {
  const t = useT();
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
      <PageHeader
        eyebrow={t("Diagnostics")}
        title={t("Health")}
        subtitle={<>{t("Certificates, certbot renewal, nginx and DNS.")}{" "}{checkedAt ? t("Checked {when}.", { when: timeAgo(new Date(checkedAt * 1000).toISOString()) }) : ""}</>}
        actions={<Button variant="primary" onClick={() => load(true)} disabled={busy}>{busy ? <Spinner /> : null}{t("Check again")}</Button>}
      />
      <ErrorText>{error}</ErrorText>
      {checks && (
        <div className="flex gap-3 text-sm">
          <Badge tone="red">{t("{n} error(s)", { n: count("error") })}</Badge>
          <Badge tone="amber">{t("{n} warning(s)", { n: count("warning") })}</Badge>
          <Badge tone="sky">{t("{n} info", { n: count("info") })}</Badge>
        </div>
      )}
      {checks && checks.length === 0 && (
        <p className="rounded-lg border border-emerald-300 bg-emerald-50 p-4 text-sm text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300">
          ✓ {t("No problem found.")}
        </p>
      )}
      <ul className="space-y-3">
        {(checks ?? []).map((c) => (
          <li key={`${c.category}-${c.title}`} className={`glass animate-fade-up rounded-2xl border-l-2 p-5 ${c.level === "error" ? "border-l-rose-500" : c.level === "warning" ? "border-l-amber-400" : "border-l-sky-400"}`}>
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={TONE[c.level]}>{c.level === "error" ? t("error") : c.level === "warning" ? t("warning") : t("info")}</Badge>
              <span className="font-medium">{c.title}</span>
              <span className="text-xs text-zinc-500">{c.category}</span>
            </div>
            {c.detail && <p className="mt-1.5 whitespace-pre-wrap text-sm text-zinc-600 dark:text-zinc-400">{c.detail}</p>}
            {c.fix && (
              <div className="mt-2 space-y-1">
                <p className="text-xs text-zinc-500">{t("Suggested fix (run on the server):")}</p>
                <CopyCode text={c.fix} />
              </div>
            )}
          </li>
        ))}
      </ul>
      {!checks && !error && <p className="text-sm text-zinc-500">{t("Running checks (DNS lookups can take a few seconds)…")}</p>}
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
