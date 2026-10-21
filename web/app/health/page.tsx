"use client";

import { useCallback, useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { Badge, Button, ConfirmDialog, CopyCode, ErrorText, PageHeader, Spinner } from "@/components/ui";
import { api, ApiError, timeAgo, type Check } from "@/lib/api";
import { msg, useT } from "@/lib/i18n";

const TONE = { error: "red", warning: "amber", info: "sky" } as const;

// Fix labels and confirmations come from the API in English.
const FIX_TEXTS = [
  msg("Delete the certificate"), msg("Mas checks that no configuration uses it, keeps a copy in /var/lib/mas/backups/certbot, then runs certbot delete."),
  msg("Retire the renewal file"), msg("The renewal file is moved to /root/letsencrypt-retired: certbot stops failing on it."),
  msg("Test the renewal"), msg("Runs certbot renew --dry-run: nothing changes, the output tells why it fails."),
  msg("Renew through nginx"), msg("The renewal file is backed up, switched to the nginx method (no more nginx stop), then a test renewal checks it; it is restored if the test fails."),
  msg("Turn on automatic renewal"), msg("Runs systemctl enable --now certbot.timer."),
  msg("Restart nginx under systemd"), msg("Every site is down for about a second while the stray nginx is stopped and systemd starts it again."),
];

type Result = { ok: boolean; output: string };
const key = (c: Check) => `${c.category}-${c.title}`;

function Health() {
  const t = useT();
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [checkedAt, setCheckedAt] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [asking, setAsking] = useState<Check[] | null>(null); // fixes waiting for confirmation
  const [running, setRunning] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, Result>>({});
  const [showCommand, setShowCommand] = useState<Record<string, boolean>>({});

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

  // The browser only names the fix: Mas checks the problem again and applies its own code.
  async function apply(list: Check[]) {
    setAsking(null);
    for (const c of list) {
      setRunning(key(c));
      try {
        const r = await api<Result>("/health/fix", { method: "POST", body: { action: c.action!.id, target: c.target } });
        setResults((prev) => ({ ...prev, [key(c)]: r }));
      } catch (e) {
        setResults((prev) => ({ ...prev, [key(c)]: { ok: false, output: (e as ApiError).detail } }));
      }
    }
    setRunning(null);
    load(true);
  }

  const count = (level: Check["level"]) => (checks ?? []).filter((c) => c.level === level).length;
  const unusedCerts = (checks ?? []).filter((c) => c.action?.id === "cert-delete");
  const first = asking?.[0];
  // Results stay visible once their problem is gone from the list.
  const solved = Object.entries(results).filter(([k, r]) => r.ok && !(checks ?? []).some((c) => key(c) === k));

  return (
    <div className="space-y-4">
      <PageHeader
        eyebrow={t("Diagnostics")}
        title={t("Health")}
        subtitle={<>{t("Certificates, certbot renewal, nginx and DNS.")}{" "}{checkedAt ? t("Checked {when}.", { when: timeAgo(new Date(checkedAt * 1000).toISOString()) }) : ""}</>}
        actions={<Button variant="primary" onClick={() => load(true)} disabled={busy || !!running}>{busy ? <Spinner /> : null}{t("Check again")}</Button>}
      />
      <ErrorText>{error}</ErrorText>
      {checks && (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <Badge tone="red">{t("{n} error(s)", { n: count("error") })}</Badge>
          <Badge tone="amber">{t("{n} warning(s)", { n: count("warning") })}</Badge>
          <Badge tone="sky">{t("{n} info", { n: count("info") })}</Badge>
          {unusedCerts.length > 1 && (
            <Button className="ml-auto" disabled={!!running} onClick={() => setAsking(unusedCerts)}>
              {t("Delete the {n} unused certificates", { n: unusedCerts.length })}
            </Button>
          )}
        </div>
      )}
      {solved.length > 0 && (
        <ul className="space-y-1 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-4 text-sm text-emerald-800 dark:text-emerald-300">
          {solved.map(([k]) => <li key={k}>✓ {t("Fixed: {what}", { what: k.replace(/^[a-z]+-/, "") })}</li>)}
        </ul>
      )}
      {checks && checks.length === 0 && (
        <p className="rounded-lg border border-emerald-300 bg-emerald-50 p-4 text-sm text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300">
          ✓ {t("No problem found.")}
        </p>
      )}
      <ul className="space-y-3">
        {(checks ?? []).map((c) => {
          const result = results[key(c)];
          return (
            <li key={key(c)} className={`glass animate-fade-up rounded-2xl border-l-2 p-5 ${c.level === "error" ? "border-l-rose-500" : c.level === "warning" ? "border-l-amber-400" : "border-l-sky-400"}`}>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={TONE[c.level]}>{c.level === "error" ? t("error") : c.level === "warning" ? t("warning") : t("info")}</Badge>
                <span className="font-medium">{c.title}</span>
                <span className="text-xs text-zinc-500">{c.category}</span>
                {c.action && (
                  <Button className="ml-auto" variant={c.action.danger ? "danger" : "primary"} disabled={!!running} onClick={() => setAsking([c])}>
                    {running === key(c) ? <Spinner /> : null}{t(c.action.label)}
                  </Button>
                )}
              </div>
              {c.detail && <p className="mt-1.5 whitespace-pre-wrap text-sm text-zinc-600 dark:text-zinc-400">{c.detail}</p>}
              {result && (
                <pre className={`mono mt-2 max-h-60 overflow-auto whitespace-pre-wrap rounded-lg border p-3 text-xs ${result.ok ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-800 dark:text-emerald-300" : "border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-300"}`}>
                  {result.ok ? "✓ " : "✗ "}{result.output}
                </pre>
              )}
              {c.fix && (c.action ? (
                <div className="mt-2">
                  <button className="text-xs text-zinc-500 hover:underline" onClick={() => setShowCommand((s) => ({ ...s, [key(c)]: !s[key(c)] }))}>
                    {showCommand[key(c)] ? t("Hide the command") : t("Or by hand, on the server")}
                  </button>
                  {showCommand[key(c)] && <div className="mt-1"><CopyCode text={c.fix} /></div>}
                </div>
              ) : (
                <div className="mt-2 space-y-1">
                  <p className="text-xs text-zinc-500">{t("Suggested fix (run on the server):")}</p>
                  <CopyCode text={c.fix} />
                </div>
              ))}
            </li>
          );
        })}
      </ul>
      {!checks && !error && <p className="text-sm text-zinc-500">{t("Running checks (DNS lookups can take a few seconds)…")}</p>}

      <ConfirmDialog
        open={!!first}
        danger={!!asking?.some((c) => c.action?.danger)}
        title={asking && asking.length > 1 ? t("Delete the {n} unused certificates", { n: asking.length }) : first?.action ? `${t(first.action.label)} · ${first.target}` : ""}
        message={first?.action ? <span className="block space-y-2">
          <span className="block">{t(first.action.confirm)}</span>
          {asking && asking.length > 1 && <span className="mono block text-xs">{asking.map((c) => c.target).join(", ")}</span>}
        </span> : null}
        confirmLabel={first?.action ? t(first.action.label) : ""}
        onConfirm={() => asking && apply(asking)}
        onCancel={() => setAsking(null)}
      />
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
