"use client";

import { useEffect, useState } from "react";
import { Eye, Globe, Users } from "lucide-react";
import { CmsLayout, NoSite, PageTitle, useCms } from "@/components/cms/CmsLayout";
import { StatCard } from "@/components/cms/common";
import { TrafficChart } from "@/components/cms/TrafficChart";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/cms/ui/card";
import { Skeleton } from "@/components/cms/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/cms/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/cms/ui/tabs";
import { api, type ApiError, type TrafficReport } from "@/lib/api";
import { useI18n, useT } from "@/lib/i18n";

function Stats() {
  const t = useT();
  const { lang } = useI18n();
  const { site } = useCms();
  const [days, setDays] = useState("30");
  const [data, setData] = useState<TrafficReport | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!site) return;
    setData(null);
    api<TrafficReport>(`/cms/sites/${encodeURIComponent(site.id)}/stats?days=${days}`).then(setData).catch((e: ApiError) => setError(e.detail));
  }, [site, days]);

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
          <div className="grid gap-4 sm:grid-cols-3">
            {!data ? Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="h-28" />) : (
              <>
                <StatCard label={t("Page views")} value={nf(data.totals!.pageviews)} icon={<Eye />} />
                <StatCard label={t("Visitors")} value={nf(data.totals!.visitors)} icon={<Users />} hint={t("Sum of the daily visitors")} />
                <StatCard label={t("Requests")} value={nf(data.totals!.requests)} icon={<Globe />} hint={t("Bots and files included")} />
              </>
            )}
          </div>
          <Card className="mt-6">
            <CardHeader><CardTitle>{t("Traffic")}</CardTitle><CardDescription>{t("Counted from the server logs: no cookie, no IP address kept.")}</CardDescription></CardHeader>
            <CardContent>{data ? <TrafficChart days={data.days} lang={lang} /> : <Skeleton className="h-64" />}</CardContent>
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
