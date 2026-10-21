"use client";

import { useEffect, useState } from "react";
import { Card, ErrorText } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { msg, useT } from "@/lib/i18n";

interface SiteStats {
  shared_log?: boolean;
  reason?: string;
  days: { day: string; requests: number; pageviews: number; visitors: number }[];
  totals: { requests: number; pageviews: number; visitors: number } | null;
  top_pages: { path: string; views: number }[];
  since: string | null;
  tracked_since: string | null;
}
type Metric = "requests" | "pageviews" | "visitors";
const METRICS: { id: Metric; label: string }[] = [
  { id: "requests", label: msg("Requests") }, { id: "pageviews", label: msg("Page views") }, { id: "visitors", label: msg("Visitors") },
];
const fmtDay = (day: string, lang: string) => new Date(`${day}T12:00:00`).toLocaleDateString(lang, { day: "numeric", month: "short" });

/** Visits of a site per day, counted from its server logs (bots and files left out of page views). */
export function TrafficStats({ url, title }: { url: string; title?: string }) {
  const t = useT();
  const lang = typeof navigator !== "undefined" ? navigator.language : "fr";
  const [days, setDays] = useState(30);
  const [metric, setMetric] = useState<Metric>("requests");
  const [data, setData] = useState<SiteStats | null>(null);
  const [error, setError] = useState("");
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    setData(null);
    api<SiteStats>(`${url}?days=${days}`).then(setData).catch((e: ApiError) => setError(e.detail));
  }, [url, days]);

  const series = data?.days ?? [];
  const max = Math.max(1, ...series.map((d) => d[metric]));
  const shown = hover !== null ? series[hover] : null;
  const label = METRICS.find((m) => m.id === metric)!.label;
  return (
    <Card title={title ?? t("Statistics")} actions={
      <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label={t("Period")} className="rounded border border-zinc-300 bg-transparent px-1.5 py-1 text-xs dark:border-zinc-700">
        {[7, 30, 90, 365].map((n) => <option key={n} value={n}>{t("{n} days", { n })}</option>)}
      </select>
    }>
      <ErrorText>{error}</ErrorText>
      {!data && !error && <div className="glass shimmer h-48 rounded-xl" />}
      {data && !data.totals && <p className="text-sm text-zinc-500">{t("No statistics: the site has no domain yet.")}</p>}
      {data?.totals && (
        <div className="space-y-6">
          <div className="grid grid-cols-3 gap-3">
            {METRICS.map((m) => (
              <button key={m.id} type="button" onClick={() => setMetric(m.id)} aria-pressed={metric === m.id}
                className={`rounded-xl border p-3 text-left transition ${metric === m.id ? "border-indigo-400/60 bg-indigo-500/10" : "border-zinc-200 hover:bg-zinc-50 dark:border-white/[0.08] dark:hover:bg-white/[0.04]"}`}>
                <div className="text-xs text-zinc-500">{t(m.label)}</div>
                <div className="mt-1 text-2xl font-semibold tabular-nums">{data.totals![m.id].toLocaleString(lang)}</div>
              </button>
            ))}
          </div>

          <div>
            <div className="mb-2 flex items-baseline justify-between gap-2 text-xs text-zinc-500">
              <span>{t("{metric} per day", { metric: t(label) })}</span>
              <span className="tabular-nums">{shown ? `${fmtDay(shown.day, lang)} · ${shown[metric].toLocaleString(lang)}` : t("max {n}", { n: max.toLocaleString(lang) })}</span>
            </div>
            <div className="relative flex h-40 items-end gap-[2px] border-b border-zinc-200 dark:border-white/[0.08]" onMouseLeave={() => setHover(null)}>
              {series.map((d, i) => (
                <div key={d.day} className="flex h-full flex-1 items-end" onMouseEnter={() => setHover(i)}
                  title={`${fmtDay(d.day, lang)} : ${d.requests} ${t("requests")}, ${d.pageviews} ${t("page views")}, ${d.visitors} ${t("visitors")}`}>
                  <div className={`w-full rounded-t-[4px] transition-colors ${hover === i ? "bg-indigo-400" : "bg-indigo-500"}`}
                    style={{ height: `${d[metric] ? Math.max(2, (d[metric] / max) * 100) : 0}%` }} />
                </div>
              ))}
            </div>
            <div className="mt-1 flex justify-between text-[11px] text-zinc-500">
              <span>{series[0] && fmtDay(series[0].day, lang)}</span>
              <span>{series.length > 0 && fmtDay(series[series.length - 1].day, lang)}</span>
            </div>
          </div>

          {data.top_pages.length > 0 && (
            <div>
              <div className="mb-2 text-xs text-zinc-500">{t("Most viewed pages")}</div>
              <ul className="divide-y divide-zinc-100 text-sm dark:divide-white/[0.06]">
                {data.top_pages.map((p) => (
                  <li key={p.path} className="flex justify-between gap-3 py-1.5"><span className="mono truncate text-xs">{p.path}</span><span className="tabular-nums text-zinc-500">{p.views.toLocaleString(lang)}</span></li>
                ))}
              </ul>
            </div>
          )}

          <details className="text-sm">
            <summary className="cursor-pointer text-xs text-zinc-500">{t("Table")}</summary>
            <table className="mt-2 w-full text-xs tabular-nums">
              <thead><tr className="text-left text-zinc-500"><th className="py-1 font-normal">{t("Day")}</th><th className="font-normal">{t("Requests")}</th><th className="font-normal">{t("Page views")}</th><th className="font-normal">{t("Visitors")}</th></tr></thead>
              <tbody>{[...series].reverse().map((d) => <tr key={d.day}><td className="py-0.5">{fmtDay(d.day, lang)}</td><td>{d.requests}</td><td>{d.pageviews}</td><td>{d.visitors}</td></tr>)}</tbody>
            </table>
          </details>

          <p className="text-xs text-zinc-500">
            {t("Counted from the server logs, without cookies: no address is kept. Page views leave out robots, images and scripts; a visitor is counted once per day.")}
            {data.tracked_since && ` ${t("Data since {date}.", { date: new Date(`${data.tracked_since}T12:00:00`).toLocaleDateString(lang) })}`}
            {data.shared_log && ` ${t("This site has no access log of its own: Mas counts its lines of a log that names the site, from the moment it was set up.")}`}
          </p>
        </div>
      )}
    </Card>
  );
}
