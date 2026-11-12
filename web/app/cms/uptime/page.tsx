"use client";

import { useEffect, useState } from "react";
import { Activity, CheckCircle2, Clock, Copy, ExternalLink, XCircle } from "lucide-react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { toast } from "sonner";
import { CmsLayout, NoSite, PageTitle, useCms } from "@/components/cms/CmsLayout";
import { StatCard, formatDate } from "@/components/cms/common";
import { Badge } from "@/components/cms/ui/badge";
import { Button } from "@/components/cms/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/cms/ui/card";
import { Label } from "@/components/cms/ui/label";
import { Skeleton } from "@/components/cms/ui/skeleton";
import { Switch } from "@/components/cms/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/cms/ui/table";
import { api, timeAgo, type ApiError, type UptimeReport } from "@/lib/api";
import { useI18n, useT } from "@/lib/i18n";
import { BRAND } from "@/lib/brand";

function Uptime() {
  const t = useT();
  const { lang } = useI18n();
  const { site, session } = useCms();
  const [r, setR] = useState<UptimeReport | null>(null);
  const [error, setError] = useState("");
  const base = site ? `/cms/sites/${encodeURIComponent(site.id)}/uptime` : "";

  useEffect(() => {
    if (!site) return;
    setR(null);
    const load = () => api<UptimeReport>(`${base}?days=30`).then(setR).catch((e: ApiError) => setError(e.detail));
    load();
    const timer = setInterval(load, 60_000);
    return () => clearInterval(timer);
  }, [site, base]);

  if (!site) return <NoSite />;
  if (!site.domain) return <><PageTitle title={t("Availability")} /><Card className="p-8 text-center text-sm text-muted-foreground">{t("Give the site a domain first: the audit reads the live site.")}</Card></>;
  const pageUrl = typeof window === "undefined" ? "" : `${window.location.origin}/status/${site.id}`;
  const bar = (u: number | null) => (u === null ? "bg-muted" : u >= 99.9 ? "bg-emerald-500" : u >= 95 ? "bg-amber-500" : "bg-red-500");

  return (
    <>
      <PageTitle title={t("Availability")} description={t("Mas opens your site every minute and keeps the answers.")} />
      {error && <p className="mb-4 text-sm text-destructive">{error}</p>}
      {!r ? <Skeleton className="h-64" /> : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard label={t("Now")} value={r.up_now === null ? "—" : r.up_now ? t("Online") : t("Down")} icon={r.up_now === false ? <XCircle className="text-red-500" /> : <CheckCircle2 className="text-emerald-500" />}
              hint={r.last_check ? `${t("Checked")} ${timeAgo(r.last_check)}` : t("No check yet")} />
            <StatCard label={t("Availability · 30 days")} value={r.uptime === null ? "—" : `${r.uptime}%`} icon={<Activity />} hint={`${r.checks} ${t("checks")}`} />
            <StatCard label={t("Average response")} value={r.avg_ms === null ? "—" : `${r.avg_ms} ms`} icon={<Clock />} />
            <StatCard label={t("Incidents")} value={r.incidents.length} icon={<XCircle />} />
          </div>

          <Card className="mt-6">
            <CardHeader><CardTitle>{t("Last 30 days")}</CardTitle><CardDescription>{t("One bar per day: green is fully available.")}</CardDescription></CardHeader>
            <CardContent>
              <div className="flex h-10 gap-0.5">{r.days.map((d) => <div key={d.day} title={`${d.day}: ${d.uptime === null ? t("no data") : `${d.uptime}%`}`} className={`flex-1 rounded-sm ${bar(d.uptime)}`} />)}</div>
            </CardContent>
          </Card>

          <div className="mt-6 grid gap-6 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader><CardTitle>{t("Response time · 24 hours")}</CardTitle></CardHeader>
              <CardContent>
                {r.latency.length < 2 ? <p className="py-12 text-center text-sm text-muted-foreground">{t("Not enough data yet.")}</p> : (
                  <div className="h-48">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={r.latency} margin={{ left: -16, right: 8 }}>
                        <XAxis dataKey="hour" tickFormatter={(h: string) => `${h.slice(11, 13)}h`} tick={{ fontSize: 11, fill: "currentColor", opacity: 0.6 }} axisLine={false} tickLine={false} />
                        <YAxis tick={{ fontSize: 11, fill: "currentColor", opacity: 0.6 }} axisLine={false} tickLine={false} unit=" ms" width={56} />
                        <Tooltip contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12, color: "var(--popover-foreground)" }} formatter={(v) => [`${v} ms`, t("Response")]} />
                        <Area type="monotone" dataKey="ms" stroke={BRAND} strokeWidth={2} fill={BRAND} fillOpacity={0.15} />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>{t("Public status page")}</CardTitle><CardDescription>{t("A simple page your clients can open to see if the site is up.")}</CardDescription></CardHeader>
              <CardContent className="space-y-3">
                {session.role === "admin" ? (
                  <div className="flex items-center gap-3"><Switch id="sp" checked={r.status_page} onCheckedChange={async (on) => { try { await api(base, { method: "PUT", body: { status_page: on } }); setR({ ...r, status_page: on }); } catch (e) { toast.error((e as ApiError).detail); } }} /><Label htmlFor="sp" className="font-normal">{t("Publish the status page")}</Label></div>
                ) : <p className="text-sm text-muted-foreground">{r.status_page ? t("The status page is published.") : t("The status page is not published.")}</p>}
                {r.status_page && (
                  <div className="flex items-center gap-2 rounded-md border bg-muted/40 p-2"><code className="min-w-0 flex-1 truncate text-xs">{pageUrl}</code>
                    <Button variant="ghost" size="icon" className="size-7" aria-label={t("Copy")} onClick={() => { navigator.clipboard?.writeText(pageUrl); toast.success(t("Copied.")); }}><Copy /></Button>
                    <Button asChild variant="ghost" size="icon" className="size-7" aria-label={t("Open")}><a href={pageUrl} target="_blank" rel="noreferrer"><ExternalLink /></a></Button></div>
                )}
              </CardContent>
            </Card>
          </div>

          <Card className="mt-6">
            <CardHeader><CardTitle>{t("Incidents")}</CardTitle></CardHeader>
            <CardContent className="px-2 pb-2">
              <Table>
                <TableHeader><TableRow><TableHead>{t("Started")}</TableHead><TableHead>{t("Reason")}</TableHead><TableHead>{t("Status")}</TableHead></TableRow></TableHeader>
                <TableBody>
                  {r.incidents.length === 0 && <TableRow><TableCell colSpan={3} className="py-8 text-center text-muted-foreground">{t("No incident in the last 30 days.")}</TableCell></TableRow>}
                  {r.incidents.map((i) => (
                    <TableRow key={i.id}>
                      <TableCell className="whitespace-nowrap">{formatDate(`${i.started}`, lang)}</TableCell>
                      <TableCell>{i.reason}</TableCell>
                      <TableCell>{i.ended ? <Badge variant="success">{t("Resolved")}</Badge> : <Badge variant="destructive">{t("Ongoing")}</Badge>}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}
    </>
  );
}

export default function UptimePage() {
  return <CmsLayout><Uptime /></CmsLayout>;
}
