"use client";

/** Like Vercel: does each name of an app (its domain, its www…) point to this server, and what to add. */

import { useCallback, useEffect, useState } from "react";
import { Button, Spinner } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface DomainCheck {
  name: string;
  status: "ok" | "elsewhere" | "missing";
  ips: string[];
  records: { type: string; name: string; value: string }[];
}

// Same list as mas/domains.py: "shop.co.uk" is a root domain like "shop.com".
const SECOND_LEVEL = new Set(["co.uk", "org.uk", "me.uk", "ac.uk", "gov.uk", "ltd.uk", "plc.uk", "com.au", "net.au", "org.au", "co.nz",
  "org.nz", "co.jp", "ne.jp", "or.jp", "com.br", "net.br", "com.mx", "com.ar", "com.tr", "co.za", "co.in", "co.kr", "com.cn", "com.hk",
  "com.sg", "com.tw", "co.il", "com.pl", "co.at", "or.at", "asso.fr", "gouv.fr"]);

export function isRootDomain(domain: string): boolean {
  const labels = domain.toLowerCase().split(".");
  return labels[0] !== "www" && (labels.length === 2 || (labels.length === 3 && SECOND_LEVEL.has(labels.slice(1).join("."))));
}

function Pill({ status }: { status: DomainCheck["status"] }) {
  const t = useT();
  const style = {
    ok: "border-emerald-400/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
    elsewhere: "border-amber-400/30 bg-amber-400/10 text-amber-700 dark:text-amber-300",
    missing: "border-rose-400/30 bg-rose-500/10 text-rose-600 dark:text-rose-300",
  }[status];
  return (
    <span className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ${style}`}>
      {status === "ok" ? "✓" : "!"} {status === "ok" ? t("Valid configuration") : status === "elsewhere" ? t("Points elsewhere") : t("Invalid configuration")}
    </span>
  );
}

export function DomainStatus({ appId }: { appId: string }) {
  const t = useT();
  const [checks, setChecks] = useState<DomainCheck[] | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    api<{ domains: DomainCheck[] }>(`/apps/${encodeURIComponent(appId)}/domains`)
      .then((d) => { setChecks(d.domains); setError(""); })
      .catch((e: ApiError) => setError(e.detail))
      .finally(() => setLoading(false));
  }, [appId]);

  const invalid = (checks ?? []).some((c) => c.status !== "ok");
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {  // a record just created: watch it appear
    if (!invalid) return;
    const timer = setInterval(load, 30_000);
    return () => clearInterval(timer);
  }, [invalid, load]);

  if (error) return <p className="text-xs text-rose-500">{t(error)}</p>;
  if (!checks) return <p className="text-xs text-zinc-500"><Spinner /> {t("Checking DNS…")}</p>;

  return (
    <div className="space-y-3">
      {checks.map((c) => (
        <div key={c.name} className="rounded-xl border border-zinc-200/80 dark:border-white/[0.06]">
          <div className="flex flex-wrap items-center gap-2 px-3 py-2.5">
            <a href={`https://${c.name}`} target="_blank" rel="noreferrer" className="min-w-0 flex-1 truncate text-sm font-medium hover:underline">{c.name}</a>
            <Pill status={c.status} />
          </div>
          {c.status !== "ok" && (
            <div className="space-y-2 border-t border-zinc-200/80 px-3 py-2.5 text-xs dark:border-white/[0.06]">
              <p className="text-zinc-500">
                {c.status === "missing" ? t("No DNS record yet. Set the following record at your registrar:") : t("It points to {ips}. Set the following record at your registrar:", { ips: c.ips.join(", ") })}
              </p>
              <table className="mono w-full text-left">
                <thead className="text-[10px] uppercase tracking-wider text-zinc-500"><tr><th className="pb-1 font-medium">{t("Type")}</th><th className="pb-1 font-medium">{t("Name")}</th><th className="pb-1 font-medium">{t("Value")}</th></tr></thead>
                <tbody>
                  {c.records.map((r, i) => (
                    <tr key={i} className={i > 0 && r.name === "www" ? "text-zinc-500" : ""}>
                      <td className="py-0.5 pr-3">{r.type}</td>
                      <td className="py-0.5 pr-3">{r.name}</td>
                      <td className="py-0.5">
                        <button type="button" title={t("Copy")} onClick={() => navigator.clipboard?.writeText(r.value)} className="hover:underline">{r.value}</button>
                        {i > 0 && r.name === "www" && <span className="ml-2 font-sans">({t("or")})</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ))}
      <div className="flex items-center justify-between gap-2 text-xs text-zinc-500">
        <span>{invalid ? t("Checked again every 30 s. DNS changes can take a few minutes.") : t("Every name points to this server.")}</span>
        <Button variant="ghost" onClick={load} disabled={loading}>{loading ? <Spinner /> : null}{t("Refresh")}</Button>
      </div>
    </div>
  );
}
