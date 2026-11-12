"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronDown, Info, Loader2, ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import { Line, LineChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import { CmsLayout, NoSite, PageTitle, useCms } from "@/components/cms/CmsLayout";
import { StatCard, formatDate } from "@/components/cms/common";
import { Badge } from "@/components/cms/ui/badge";
import { Button } from "@/components/cms/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/cms/ui/card";
import { Skeleton } from "@/components/cms/ui/skeleton";
import { api, timeAgo, type ApiError, type ComplianceAudit, type ComplianceFinding, type ComplianceReport } from "@/lib/api";
import { translateFinding } from "@/lib/complianceTexts";
import { msg, useI18n, useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const MANUAL: Record<string, string> = {
  register: msg("A register of processing activities (GDPR art. 30)"),
  processors: msg("Contracts (DPA) with every service that handles your visitors' data"),
  retention: msg("Retention periods applied: data is really deleted after them"),
  rights: msg("A way to answer access, erasure and objection requests within one month"),
  breach: msg("A procedure to report a data breach within 72 hours"),
  dpo: msg("A data protection officer or a named contact, when required"),
};
const CATEGORY: Record<string, string> = {
  security: msg("Security"), cookies: msg("Cookies"), trackers: msg("Third-party services"), consent: msg("Consent"),
  legal: msg("Legal pages"), forms: msg("Forms"),
};
const LEVEL_ICON = { critical: ShieldAlert, warning: AlertTriangle, info: Info, pass: CheckCircle2 } as const;
const LEVEL_STYLE = {
  critical: "text-red-600 dark:text-red-400", warning: "text-amber-600 dark:text-amber-400",
  info: "text-sky-600 dark:text-sky-400", pass: "text-emerald-600 dark:text-emerald-400",
} as const;

function Score({ audit }: { audit: ComplianceAudit }) {
  const t = useT();
  const color = audit.verdict === "ok" ? "#10b981" : audit.verdict === "attention" ? "#f59e0b" : "#ef4444";
  const r = 52, c = 2 * Math.PI * r;
  return (
    <div className="flex items-center gap-6">
      <div className="relative size-32 shrink-0">
        <svg viewBox="0 0 120 120" className="size-full -rotate-90">
          <circle cx="60" cy="60" r={r} fill="none" stroke="currentColor" strokeOpacity={0.12} strokeWidth="10" />
          <circle cx="60" cy="60" r={r} fill="none" stroke={color} strokeWidth="10" strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - audit.score / 100)} />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center"><span className="text-3xl font-semibold tabular-nums">{audit.score}</span><span className="text-xs text-muted-foreground">/ 100</span></div>
      </div>
      <div className="space-y-1.5">
        <div className="flex items-center gap-2 text-lg font-semibold">
          {audit.verdict === "ok" ? <ShieldCheck className="size-5 text-emerald-500" /> : audit.verdict === "attention" ? <ShieldQuestion className="size-5 text-amber-500" /> : <ShieldAlert className="size-5 text-red-500" />}
          {audit.verdict === "ok" ? t("Nothing visible is wrong") : audit.verdict === "attention" ? t("Points to improve") : t("Blocking issues found")}
        </div>
        <p className="max-w-md text-sm text-muted-foreground">
          {audit.verdict === "ok" ? t("The automatic checks found no problem. This is not a legal guarantee: see the checklist below.")
            : audit.verdict === "attention" ? t("No blocking issue, but some points deserve your attention.")
            : t("Fix the red points first: they are the ones a regulator or a complaint would start with.")}
        </p>
      </div>
    </div>
  );
}

function Finding({ f }: { f: ComplianceFinding }) {
  const t = useT();
  const [open, setOpen] = useState(f.level === "critical");
  const Icon = LEVEL_ICON[f.level];
  return (
    <div className="rounded-md border">
      <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-center gap-3 px-4 py-3 text-left">
        <Icon className={cn("size-4 shrink-0", LEVEL_STYLE[f.level])} />
        <span className="min-w-0 flex-1 text-sm font-medium">{translateFinding(f.title)}</span>
        {f.new && <Badge variant="info">{t("New")}</Badge>}
        <Badge variant="outline">{t(CATEGORY[f.category] ?? f.category)}</Badge>
        {(f.detail || f.fix) && <ChevronDown className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} />}
      </button>
      {open && (f.detail || f.fix) && (
        <div className="space-y-2 border-t px-4 py-3 text-sm">
          {f.detail && <p className="text-muted-foreground">{translateFinding(f.detail)}</p>}
          {f.fix && <p><span className="font-medium">{t("How to fix")}: </span>{translateFinding(f.fix)}</p>}
          {f.where && <p className="break-all text-xs text-muted-foreground">{f.where}</p>}
        </div>
      )}
    </div>
  );
}

function Compliance() {
  const t = useT();
  const { lang } = useI18n();
  const { site } = useCms();
  const [report, setReport] = useState<ComplianceReport | null>(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [filter, setFilter] = useState<"all" | "critical" | "warning" | "info" | "pass">("all");
  const base = site ? `/cms/sites/${encodeURIComponent(site.id)}/compliance` : "";

  useEffect(() => {
    if (!site) return;
    setReport(null);
    api<ComplianceReport>(base).then(setReport).catch((e: ApiError) => setError(e.detail));
  }, [site, base]);

  async function run() {
    setRunning(true); setError("");
    try { setReport(await api<ComplianceReport>(`${base}/run`, { method: "POST" })); }
    catch (e) { setError((e as ApiError).detail); } finally { setRunning(false); }
  }

  if (!site) return <NoSite />;
  const audit = report?.latest ?? null;
  const shown = audit?.findings.filter((f) => filter === "all" || f.level === filter) ?? [];
  const trend = [...(report?.history ?? [])].reverse();

  return (
    <>
      <PageTitle title={t("Compliance")} description={t("GDPR and consent checks on the live site, run again after every change.")}
        actions={<Button onClick={run} disabled={running || !site.domain}>{running && <Loader2 className="animate-spin" />}{t("Audit now")}</Button>} />
      {error && <p className="mb-4 text-sm text-destructive">{error}</p>}
      {!site.domain && <Card className="p-8 text-center text-sm text-muted-foreground">{t("Give the site a domain first: the audit reads the live site.")}</Card>}
      {site.domain && !report && <Skeleton className="h-64" />}
      {site.domain && report && !audit && (
        <Card className="p-10 text-center text-sm text-muted-foreground">{t("No audit yet. It runs by itself after the next publication, or start one now.")}</Card>
      )}
      {audit && (
        <>
          <Card>
            <CardContent className="flex flex-wrap items-center justify-between gap-6 p-6">
              <Score audit={audit} />
              <div className="text-sm text-muted-foreground">
                <p>{audit.host}</p>
                <p>{t("Audited")} {timeAgo(audit.at)} · {audit.trigger === "release" ? t("after a publication") : audit.trigger === "weekly" ? t("weekly check") : audit.trigger === "first" ? t("first check") : t("on request")}</p>
                <p>{t("{n} page(s) read", { n: audit.pages.length })}</p>
              </div>
            </CardContent>
          </Card>

          <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {(["critical", "warning", "info", "pass"] as const).map((lvl) => {
              const Icon = LEVEL_ICON[lvl];
              return (
                <button key={lvl} type="button" onClick={() => setFilter(filter === lvl ? "all" : lvl)} className="text-left">
                  <StatCard label={lvl === "critical" ? t("Blocking") : lvl === "warning" ? t("To improve") : lvl === "info" ? t("Advice") : t("Passed")}
                    value={audit.counts[lvl]} icon={<Icon className={LEVEL_STYLE[lvl]} />} hint={filter === lvl ? t("Showing only these") : undefined} />
                </button>
              );
            })}
          </div>

          <div className="mt-6 grid gap-6 lg:grid-cols-3">
            <div className="space-y-2 lg:col-span-2">
              {shown.length === 0 && <Card className="p-8 text-center text-sm text-muted-foreground">{t("Nothing here.")}</Card>}
              {shown.map((f) => <Finding key={`${f.id}|${f.where}`} f={f} />)}
            </div>
            <div className="space-y-6">
              {trend.length > 1 && (
                <Card>
                  <CardHeader><CardTitle>{t("Score over time")}</CardTitle><CardDescription>{t("One point per audit")}</CardDescription></CardHeader>
                  <CardContent>
                    <div className="h-28">
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={trend}>
                          <YAxis hide domain={[0, 100]} />
                          <Tooltip contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12, color: "var(--popover-foreground)" }}
                            labelFormatter={(_, p) => (p?.[0] ? formatDate(p[0].payload.at, lang) : "")} formatter={(v) => [`${v}/100`, t("Score")]} />
                          <Line type="monotone" dataKey="score" stroke="#3b82f6" strokeWidth={2} dot={{ r: 3 }} />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  </CardContent>
                </Card>
              )}
              <Card>
                <CardHeader><CardTitle>{t("What only you can check")}</CardTitle><CardDescription>{t("An audit sees the public site, not how data is handled behind it.")}</CardDescription></CardHeader>
                <CardContent><ul className="space-y-2.5 text-sm">{report?.manual.map((m) => <li key={m} className="flex gap-2"><span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-muted-foreground" />{t(MANUAL[m] ?? m)}</li>)}</ul></CardContent>
              </Card>
              <p className="text-xs text-muted-foreground">{t("Automatic technical audit, not legal advice. Rules depend on your activity and country.")}</p>
            </div>
          </div>
        </>
      )}
    </>
  );
}

export default function CompliancePage() {
  return <CmsLayout><Compliance /></CmsLayout>;
}
