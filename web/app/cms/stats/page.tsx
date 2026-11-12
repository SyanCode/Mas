"use client";

import { useEffect, useState } from "react";
import { Copy, Eye, Globe, Loader2, MapPinned, MousePointerClick, Target, Users } from "lucide-react";
import { toast } from "sonner";
import { CmsLayout, NoSite, PageTitle, useCms } from "@/components/cms/CmsLayout";
import { StatCard } from "@/components/cms/common";
import { TrafficChart } from "@/components/cms/TrafficChart";
import { CountryList, WorldMap } from "@/components/cms/WorldMap";
import { Button } from "@/components/cms/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/cms/ui/card";
import { Skeleton } from "@/components/cms/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/cms/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/cms/ui/tabs";
import { api, type ApiError, type TrafficReport } from "@/lib/api";
import { useI18n, useT } from "@/lib/i18n";

function Snippet({ code }: { code: string }) {
  const t = useT();
  return (
    <div className="flex items-start gap-2 rounded-md border bg-muted/40 p-2.5">
      <code className="min-w-0 flex-1 whitespace-pre-wrap break-all text-xs">{code}</code>
      <Button variant="ghost" size="icon" className="size-7 shrink-0" aria-label={t("Copy")} onClick={() => { navigator.clipboard?.writeText(code); toast.success(t("Copied.")); }}><Copy /></Button>
    </div>
  );
}

/** How a site starts counting conversions: one script line, then data-mas-goal on what matters. */
function TrackerSetup({ compact }: { compact?: boolean }) {
  const t = useT();
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  return (
    <details className={compact ? "text-sm" : "px-3 pb-3"} open={!compact}>
      <summary className="cursor-pointer text-sm font-medium text-muted-foreground hover:text-foreground">{compact ? t("Add or change goals") : t("Start counting conversions")}</summary>
      <div className="mt-3 space-y-3 text-sm">
        <p className="text-muted-foreground">{t("1. Add this line to every page of the site, before </body>:")}</p>
        <Snippet code={`<script defer src="${origin}/t.js"></script>`} />
        <p className="text-muted-foreground">{t("2. Name what matters with data-mas-goal. Phone and e-mail links are counted on their own.")}</p>
        <Snippet code={`<a href="/reservation" data-mas-goal="book">Book a table</a>\n<form action="/contact" data-mas-goal="contact">…</form>`} />
        <p className="text-xs text-muted-foreground">{t("No cookie, no address stored. Visitors who enable Do Not Track are not counted. Goal names: letters, digits, - _ . :")}</p>
      </div>
    </details>
  );
}

function Stats() {
  const t = useT();
  const { lang } = useI18n();
  const { site } = useCms();
  const [days, setDays] = useState("30");
  const [data, setData] = useState<TrafficReport | null>(null);
  const [error, setError] = useState("");
  const [installing, setInstalling] = useState(false);
  const [reload, setReload] = useState(0);
  const { session } = useCms();
  useEffect(() => {
    if (!site) return;
    setData(null);
    api<TrafficReport>(`/cms/sites/${encodeURIComponent(site.id)}/stats?days=${days}`).then(setData).catch((e: ApiError) => setError(e.detail));
  }, [site, days, reload]);

  async function enableMap() {
    setInstalling(true);
    setError("");
    try { await api("/cms/geo/install", { method: "POST" }); setReload((n) => n + 1); }
    catch (e) { setError((e as ApiError).detail); }
    finally { setInstalling(false); }
  }

  if (!site) return <NoSite />;
  const nf = (n: number) => new Intl.NumberFormat(lang).format(n);
  const empty = data && !data.totals;
  return (
    <>
      <PageTitle title={t("Statistics")} description={`${site.name}${site.domain ? ` · ${site.domain}` : ""}`}
        actions={<Tabs value={days} onValueChange={setDays}><TabsList><TabsTrigger value="7">7 {t("days")}</TabsTrigger><TabsTrigger value="30">30 {t("days")}</TabsTrigger><TabsTrigger value="90">90 {t("days")}</TabsTrigger></TabsList></Tabs>} />
      {error && <p className="mb-4 text-sm text-destructive">{error}</p>}
      {empty ? (
        <Card className="p-10 text-center text-sm text-muted-foreground">{t("No traffic data yet. Counting starts once the site has a domain and receives visits.")}</Card>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {!data ? Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-28" />) : (
              <>
                <StatCard label={t("Page views")} value={nf(data.totals!.pageviews)} icon={<Eye />} />
                <StatCard label={t("Visitors")} value={nf(data.totals!.visitors)} icon={<Users />} hint={t("Sum of the daily visitors")} />
                <StatCard label={t("Requests")} value={nf(data.totals!.requests)} icon={<Globe />} hint={t("Bots and files included")} />
                <StatCard label={t("Conversion rate")} icon={<Target />}
                  value={data.goals && data.goals.length > 0 ? `${data.conversion?.rate ?? 0}%` : "—"}
                  hint={data.goals && data.goals.length > 0 ? t("{n} visitors reached a goal", { n: nf(data.conversion?.visitors ?? 0) }) : t("No goal tracked yet")} />
              </>
            )}
          </div>
          <Card className="mt-6">
            <CardHeader><CardTitle>{t("Traffic")}</CardTitle><CardDescription>{t("Counted from the server logs: no cookie, no IP address kept.")}</CardDescription></CardHeader>
            <CardContent>{data ? <TrafficChart days={data.days} lang={lang} /> : <Skeleton className="h-64" />}</CardContent>
          </Card>
          <Card className="mt-6">
            <CardHeader><CardTitle>{t("Conversions")}</CardTitle><CardDescription>{t("What your visitors click, and the share of visitors who do it.")}</CardDescription></CardHeader>
            <CardContent className="px-2 pb-2">
              {!data ? <Skeleton className="m-3 h-24" /> : data.goals && data.goals.length > 0 ? (
                <Table>
                  <TableHeader><TableRow><TableHead>{t("Goal")}</TableHead><TableHead className="text-right">{t("Clicks")}</TableHead><TableHead className="text-right">{t("Visitors")}</TableHead><TableHead className="w-56">{t("Conversion rate")}</TableHead></TableRow></TableHeader>
                  <TableBody>
                    {data.goals.map((g) => (
                      <TableRow key={g.goal}>
                        <TableCell><span className="inline-flex items-center gap-2 font-medium"><MousePointerClick className="size-4 text-muted-foreground" />{g.goal}</span></TableCell>
                        <TableCell className="text-right tabular-nums">{nf(g.hits)}</TableCell>
                        <TableCell className="text-right tabular-nums">{nf(g.visitors)}</TableCell>
                        <TableCell><div className="flex items-center gap-3"><div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(100, g.rate)}%` }} /></div><span className="w-12 text-right tabular-nums">{g.rate}%</span></div></TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : <TrackerSetup />}
              {data && data.goals && data.goals.length > 0 && <div className="px-3 pb-3 pt-4"><TrackerSetup compact /></div>}
            </CardContent>
          </Card>
          <Card className="mt-6">
            <CardHeader><CardTitle>{t("Visitors by country")}</CardTitle><CardDescription>{t("Only the country is kept, never the address.")}</CardDescription></CardHeader>
            <CardContent>
              {!data ? <Skeleton className="h-72" /> : !data.geo ? (
                <div className="flex flex-col items-center gap-3 py-12 text-center">
                  <MapPinned className="size-8 text-muted-foreground" />
                  <p className="max-w-md text-sm text-muted-foreground">{t("See where your visitors come from on a world map. Needs a free country database (DB-IP), downloaded once.")}</p>
                  {session.role === "admin"
                    ? <Button onClick={enableMap} disabled={installing}>{installing && <Loader2 className="animate-spin" />}{t("Enable the world map")}</Button>
                    : <p className="text-xs text-muted-foreground">{t("Ask your administrator to enable it.")}</p>}
                </div>
              ) : data.countries && data.countries.length > 0 ? (
                <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_16rem]">
                  <WorldMap countries={data.countries} />
                  <CountryList countries={data.countries} />
                </div>
              ) : <p className="py-12 text-center text-sm text-muted-foreground">{t("Countries are counted from now on: they appear with the next visits.")}</p>}
              {data?.geo && data.cdn && (
                <div className="mt-4 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
                  <p className="font-medium">{t("Your site seems to be behind a CDN (Cloudflare)")}</p>
                  <p className="mt-1 text-muted-foreground">{t("{pct}% of the visitors come from the CDN's addresses, whose country says nothing about the visitor. Countries are left out for them. Pass the real address to nginx:", { pct: Math.round(data.cdn.share * 100) })}</p>
                  <Snippet code={data.cdn.snippet} />
                </div>
              )}
              {data?.geo && <p className="mt-4 text-[11px] text-muted-foreground">{data.attribution}</p>}
            </CardContent>
          </Card>
          <Card className="mt-6">
            <CardHeader><CardTitle>{t("Top pages")}</CardTitle></CardHeader>
            <CardContent className="px-2 pb-2">
              <Table>
                <TableHeader><TableRow><TableHead>{t("Page")}</TableHead><TableHead className="text-right">{t("Page views")}</TableHead></TableRow></TableHeader>
                <TableBody>
                  {data?.top_pages.length === 0 && <TableRow><TableCell colSpan={2} className="py-8 text-center text-muted-foreground">{t("Nothing yet.")}</TableCell></TableRow>}
                  {data?.top_pages.map((p) => <TableRow key={p.path}><TableCell className="font-mono text-xs">{p.path}</TableCell><TableCell className="text-right tabular-nums">{nf(p.views)}</TableCell></TableRow>)}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}
    </>
  );
}

export default function StatsPage() {
  return <CmsLayout><Stats /></CmsLayout>;
}
