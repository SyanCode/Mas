"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { CalendarClock, Eye, FilePlus2, Globe, Users } from "lucide-react";
import { CmsLayout, NoSite, PageTitle, useCms } from "@/components/cms/CmsLayout";
import { TrafficChart } from "@/components/cms/TrafficChart";
import { CountryList, WorldMap } from "@/components/cms/WorldMap";
import { DeployBadge, StatCard, formatDate, parseUtc, useDescribe } from "@/components/cms/common";
import { Avatar } from "@/components/cms/ui/avatar";
import { Badge } from "@/components/cms/ui/badge";
import { Button } from "@/components/cms/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/cms/ui/card";
import { Skeleton } from "@/components/cms/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/cms/ui/table";
import { api, timeAgo, type ApiError, type CmsDashboard } from "@/lib/api";
import { useI18n, useT } from "@/lib/i18n";

const nf = (n: number, lang: string) => new Intl.NumberFormat(lang).format(n);

function Dashboard() {
  const t = useT();
  const { lang } = useI18n();
  const { session, sites, site, overview } = useCms();
  const describe = useDescribe();
  const [data, setData] = useState<CmsDashboard | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<CmsDashboard>("/cms/dashboard").then(setData).catch((e: ApiError) => setError(e.detail));
  }, []);

  if (sites.length === 0) return <><PageTitle title={t("Dashboard")} /><NoSite /></>;
  const q = site ? `app=${encodeURIComponent(site.id)}` : "";
  const hasTraffic = !!data?.sites.some((s) => s.visits);

  return (
    <>
      <PageTitle title={t("Dashboard")} description={t("Welcome back, {name}.", { name: session.username })} />
      {error && <p className="mb-4 text-sm text-destructive">{error}</p>}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {!data ? Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-28" />) : (
          <>
            <StatCard label={t("Page views · 7 days")} value={hasTraffic ? nf(data.totals.pageviews, lang) : "—"} icon={<Eye />} hint={hasTraffic ? undefined : t("No traffic data yet")} />
            <StatCard label={t("Visitors · 7 days")} value={hasTraffic ? nf(data.totals.visitors, lang) : "—"} icon={<Users />} />
            <StatCard label={t("Sites")} value={data.sites.length} icon={<Globe />} />
            <StatCard label={t("Scheduled")} value={data.scheduled} icon={<CalendarClock />} hint={session.role === "admin" && data.editors != null ? t("{n} editor(s)", { n: data.editors }) : undefined} />
          </>
        )}
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader><CardTitle>{t("Traffic")}</CardTitle><CardDescription>{t("Page views and visitors per day, all your sites")}</CardDescription></CardHeader>
          <CardContent>
            {!data ? <Skeleton className="h-64" /> : hasTraffic
              ? <TrafficChart days={data.days} lang={lang} />
              : <p className="py-16 text-center text-sm text-muted-foreground">{t("Visits appear here once your sites have a domain and receive traffic.")}</p>}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>{t("At a glance")}</CardTitle><CardDescription>{site?.name}</CardDescription></CardHeader>
          <CardContent className="space-y-4">
            {!overview ? <Skeleton className="h-32" /> : overview.collections.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("This site only has pages.")}</p>
            ) : overview.collections.map((c) => (
              <div key={c.id} className="space-y-2">
                <div className="flex items-center justify-between">
                  <Link href={`/cms/content/?${q}&c=${encodeURIComponent(c.id)}`} className="font-medium hover:underline">{c.label}</Link>
                  <Button asChild size="sm" variant="outline"><Link href={`/cms/entry/?${q}&c=${encodeURIComponent(c.id)}`}><FilePlus2 />{t("New")}</Link></Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  {t("{n} published", { n: c.published })}{c.drafts && ` · ${t("{n} draft(s)", { n: c.draft })} · ${t("{n} scheduled", { n: c.scheduled })}`}
                </p>
                {c.error && <p className="text-xs text-destructive">{c.error}</p>}
              </div>
            ))}
            {site?.last_release && (
              <div className="flex items-center justify-between border-t pt-3 text-xs text-muted-foreground">
                <span>{t("Last publication")} · {timeAgo(site.last_release.created_at)}</span>
                <DeployBadge status={site.last_release.status} />
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {data && data.countries.length > 0 && (
        <Card className="mt-6">
          <CardHeader><CardTitle>{t("Visitors by country")}</CardTitle><CardDescription>{t("Last 7 days, all your sites")}</CardDescription></CardHeader>
          <CardContent className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_16rem]"><WorldMap countries={data.countries} /><CountryList countries={data.countries} limit={6} /></CardContent>
        </Card>
      )}

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader><CardTitle>{t("Your sites")}</CardTitle></CardHeader>
          <CardContent className="px-2 pb-2">
            <Table>
              <TableHeader><TableRow><TableHead>{t("Site")}</TableHead><TableHead className="text-right">{t("Page views · 7 days")}</TableHead><TableHead>{t("Last publication")}</TableHead><TableHead>{t("Compliance")}</TableHead><TableHead /></TableRow></TableHeader>
              <TableBody>
                {data?.sites.map((s) => (
                  <TableRow key={s.id}>
                    <TableCell><div className="font-medium">{s.name}</div><div className="text-xs text-muted-foreground">{s.domain ?? t("no domain yet")}</div></TableCell>
                    <TableCell className="text-right tabular-nums">{s.visits?.totals ? nf(s.visits.totals.pageviews, lang) : "—"}</TableCell>
                    <TableCell>{s.last_release ? <span className="flex items-center gap-2"><DeployBadge status={s.last_release.status} /><span className="text-xs text-muted-foreground">{timeAgo(s.last_release.created_at)}</span></span> : <span className="text-xs text-muted-foreground">{t("Not published yet")}</span>}</TableCell>
                    <TableCell>{s.compliance ? <Link href={`/cms/compliance/?app=${encodeURIComponent(s.id)}`}><Badge variant={s.compliance.verdict === "ok" ? "success" : s.compliance.verdict === "attention" ? "warning" : "destructive"}>{s.compliance.score}/100</Badge></Link> : <span className="text-xs text-muted-foreground">—</span>}</TableCell>
                    <TableCell className="text-right"><Button asChild size="sm" variant="outline"><Link href={`/cms/?app=${encodeURIComponent(s.id)}`} onClick={() => { try { localStorage.setItem("mas-cms-site", s.id); } catch {} }}>{t("Select")}</Link></Button></TableCell>
                  </TableRow>
                ))}
                {!data && <TableRow><TableCell colSpan={5}><Skeleton className="h-8" /></TableCell></TableRow>}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0"><CardTitle>{t("Recent activity")}</CardTitle><Button asChild variant="link" size="sm" className="h-auto p-0"><Link href={`/cms/activity/?${q}`}>{t("See all")}</Link></Button></CardHeader>
          <CardContent className="space-y-4">
            {!data ? <Skeleton className="h-40" /> : data.activity.length === 0 ? <p className="text-sm text-muted-foreground">{t("Nothing yet.")}</p> : data.activity.slice(0, 7).map((a) => (
              <div key={a.id} className="flex items-start gap-3">
                <Avatar name={a.actor} className="size-7 text-[10px]" />
                <div className="min-w-0 text-sm"><p className="break-words"><span className="font-medium">{a.actor}</span> {describe(a)}</p><p className="text-xs text-muted-foreground">{a.site ? `${a.site} · ` : ""}{timeAgo(a.at)}</p></div>
              </div>
            ))}
            {data && data.scheduled > 0 && (
              <div className="border-t pt-3 text-xs text-muted-foreground">
                {data.sites.flatMap((s) => s.scheduled).sort((a, b) => a.publish_at.localeCompare(b.publish_at)).slice(0, 1).map((r) => (
                  <span key={r.slug}>{t("Next publication")}: {r.cid}/{r.slug} · {formatDate(parseUtc(r.publish_at).toISOString(), lang)}</span>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}

export default function CmsHome() {
  return <CmsLayout><Dashboard /></CmsLayout>;
}
