"use client";

import { useEffect, useMemo, useState } from "react";
import { Copy, Loader2, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { CmsLayout, NoSite, PageTitle, useCms } from "@/components/cms/CmsLayout";
import { StatCard, formatDate } from "@/components/cms/common";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/cms/ui/table";
import { Button } from "@/components/cms/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/cms/ui/card";
import { Input, Textarea } from "@/components/cms/ui/input";
import { Label } from "@/components/cms/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/cms/ui/select";
import { Skeleton } from "@/components/cms/ui/skeleton";
import { Switch } from "@/components/cms/ui/switch";
import { api, type ApiError } from "@/lib/api";
import { useI18n, useT } from "@/lib/i18n";
import { BarChart3, Cookie, Download, ShieldCheck, ShieldX } from "lucide-react";

interface Config {
  enabled: boolean; position: string; theme: string; accent: string; policy_url: string; title: string; message: string;
  categories: string[]; expiry_months: number; reopen_button: boolean;
}
interface Stats { accept: number; reject: number; custom: number; total: number; accept_rate: number; logged?: number; version?: string }
interface LogRow { at: string; consent_id: string; decision: string; categories: Record<string, boolean>; banner: string }
const CATEGORIES = ["analytics", "marketing", "preferences"] as const;

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return <div className="space-y-1.5"><Label>{label}</Label>{children}{hint && <p className="text-xs text-muted-foreground">{hint}</p>}</div>;
}

function Preview({ config, lang }: { config: Config; lang: string }) {
  const [run, setRun] = useState(0);
  const doc = useMemo(() => {
    const json = JSON.stringify({ ...config, enabled: true }).replace(/</g, "\\u003c");
    return `<!doctype html><html lang="${lang}"><body style="margin:0;font-family:system-ui;background:#f4f4f5;color:#71717a"><div style="padding:24px;font-size:13px">Your site</div><script>window.__MAS_COOKIES_PREVIEW__=${json}</script><script src="/cookies.js"></script></body></html>`;
  }, [config, lang, run]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="relative">
      <iframe key={run} title="preview" srcDoc={doc} className="h-[26rem] w-full rounded-md border bg-white" />
      <Button variant="ghost" size="icon" className="absolute right-2 top-2 size-7" onClick={() => setRun((n) => n + 1)} aria-label="Reset"><RotateCcw /></Button>
    </div>
  );
}

function Cookies() {
  const t = useT();
  const { lang } = useI18n();
  const { site } = useCms();
  const [config, setConfig] = useState<Config | null>(null);
  const [saved, setSaved] = useState("");
  const [stats, setStats] = useState<Stats | null>(null);
  const [log, setLog] = useState<LogRow[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const base = site ? `/cms/sites/${encodeURIComponent(site.id)}/cookies` : "";

  useEffect(() => {
    if (!site) return;
    api<{ config: Config; stats: Stats; log: LogRow[] }>(base).then((r) => { setConfig(r.config); setSaved(JSON.stringify(r.config)); setStats(r.stats); setLog(r.log); }).catch((e: ApiError) => setError(e.detail));
  }, [site, base]);

  if (!site) return <NoSite />;
  const dirty = config !== null && JSON.stringify(config) !== saved;
  const set = (patch: Partial<Config>) => setConfig((c) => (c ? { ...c, ...patch } : c));
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  const snippet = `<script defer src="${origin}/cookies.js"></script>`;

  async function save() {
    setBusy(true); setError("");
    try {
      const r = await api<{ config: Config; stats: Stats }>(base, { method: "PUT", body: config });
      setConfig(r.config); setSaved(JSON.stringify(r.config)); setStats(r.stats); toast.success(t("Saved."));
    } catch (e) { setError((e as ApiError).detail); } finally { setBusy(false); }
  }

  return (
    <>
      <PageTitle title={t("Cookie banner")} description={t("A consent pop-up for your site, with equal Accept and Reject buttons.")}
        actions={<Button onClick={save} disabled={!dirty || busy}>{busy && <Loader2 className="animate-spin" />}{t("Save")}</Button>} />
      {error && <p className="mb-4 text-sm text-destructive">{error}</p>}
      {!config ? <Skeleton className="h-96" /> : (
        <>
          <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard label={t("Choices · 30 days")} value={stats?.total ?? 0} icon={<Cookie />} />
            <StatCard label={t("Accepted")} value={stats?.accept ?? 0} icon={<ShieldCheck />} hint={stats?.total ? `${stats.accept_rate}%` : undefined} />
            <StatCard label={t("Rejected")} value={stats?.reject ?? 0} icon={<ShieldX />} />
            <StatCard label={t("Customized")} value={stats?.custom ?? 0} icon={<BarChart3 />} />
          </div>
          <Card className="mb-6">
            <CardHeader className="flex-row items-start justify-between space-y-0">
              <div className="space-y-1.5">
                <CardTitle>{t("Proof of consent")}</CardTitle>
                <CardDescription>{t("Each choice is kept with a random id, the date, the categories and the banner version the visitor saw. No address. Kept 13 months.")}</CardDescription>
              </div>
              <Button asChild variant="outline" size="sm"><a href={`/api${base}/log.csv`} download><Download />{t("Export CSV")}</a></Button>
            </CardHeader>
            <CardContent className="px-2 pb-2">
              <Table>
                <TableHeader><TableRow><TableHead>{t("When")}</TableHead><TableHead>{t("Choice")}</TableHead><TableHead>{t("Categories")}</TableHead><TableHead>{t("Banner version")}</TableHead></TableRow></TableHeader>
                <TableBody>
                  {log.length === 0 && <TableRow><TableCell colSpan={4} className="py-8 text-center text-muted-foreground">{t("No proof yet. It is kept from the next visitor choice.")}</TableCell></TableRow>}
                  {log.map((r) => (
                    <TableRow key={r.consent_id + r.at}>
                      <TableCell className="whitespace-nowrap text-muted-foreground">{formatDate(r.at, lang)}</TableCell>
                      <TableCell>{r.decision === "accept" ? t("Accepted") : r.decision === "reject" ? t("Rejected") : t("Customized")}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">{Object.entries(r.categories).filter(([, v]) => v).map(([k]) => k).join(", ") || "—"}</TableCell>
                      <TableCell className="font-mono text-xs">{r.banner}{r.banner === stats?.version ? ` · ${t("current")}` : ""}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader><CardTitle>{t("Settings")}</CardTitle></CardHeader>
              <CardContent className="space-y-5">
                <div className="flex items-center justify-between gap-3">
                  <div><Label htmlFor="en">{t("Show the banner on the site")}</Label><p className="text-xs text-muted-foreground">{t("Off until the script is added to the site.")}</p></div>
                  <Switch id="en" checked={config.enabled} onCheckedChange={(v) => set({ enabled: v })} />
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Row label={t("Position")}>
                    <Select value={config.position} onValueChange={(v) => set({ position: v })}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent><SelectItem value="bottom-left">{t("Bottom left")}</SelectItem><SelectItem value="bottom">{t("Bottom bar")}</SelectItem><SelectItem value="center">{t("Centered")}</SelectItem></SelectContent>
                    </Select>
                  </Row>
                  <Row label={t("Theme")}>
                    <Select value={config.theme} onValueChange={(v) => set({ theme: v })}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent><SelectItem value="auto">{t("Automatic")}</SelectItem><SelectItem value="light">{t("Light")}</SelectItem><SelectItem value="dark">{t("Dark")}</SelectItem></SelectContent>
                    </Select>
                  </Row>
                </div>
                <Row label={t("Accent colour")}>
                  <div className="flex items-center gap-2">
                    <input type="color" value={config.accent} onChange={(e) => set({ accent: e.target.value })} className="h-9 w-12 cursor-pointer rounded-md border border-input bg-transparent p-1" />
                    <Input value={config.accent} onChange={(e) => set({ accent: e.target.value })} className="max-w-[8rem]" />
                  </div>
                </Row>
                <Row label={t("Cookies your site uses")} hint={t("Strictly necessary cookies are always allowed. Pick the others the site sets.")}>
                  <div className="space-y-2.5">
                    {CATEGORIES.map((c) => (
                      <div key={c} className="flex items-center gap-3">
                        <Switch id={`c-${c}`} checked={config.categories.includes(c)}
                          onCheckedChange={(on) => set({ categories: on ? [...config.categories, c] : config.categories.filter((x) => x !== c) })} />
                        <Label htmlFor={`c-${c}`} className="font-normal">{c === "analytics" ? t("Audience measurement") : c === "marketing" ? t("Marketing") : t("Preferences")}</Label>
                      </div>
                    ))}
                  </div>
                </Row>
                <Row label={t("Title")} hint={t("Empty: a default text in the visitor's language (fr, en, es, de).")}>
                  <Input value={config.title} maxLength={80} onChange={(e) => set({ title: e.target.value })} />
                </Row>
                <Row label={t("Message")}>
                  <Textarea value={config.message} maxLength={500} rows={3} onChange={(e) => set({ message: e.target.value })} />
                </Row>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Row label={t("Privacy policy link")} hint="https://… / /privacy"><Input value={config.policy_url} onChange={(e) => set({ policy_url: e.target.value })} placeholder="/privacy" /></Row>
                  <Row label={t("Ask again after (months)")} hint={t("13 months at most.")}>
                    <Input type="number" min={1} max={13} value={config.expiry_months} onChange={(e) => set({ expiry_months: Number(e.target.value) })} />
                  </Row>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <Label htmlFor="re" className="font-normal">{t("Keep a small button to change the choice")}</Label>
                  <Switch id="re" checked={config.reopen_button} onCheckedChange={(v) => set({ reopen_button: v })} />
                </div>
              </CardContent>
            </Card>

            <div className="space-y-6">
              <Card>
                <CardHeader><CardTitle>{t("Preview")}</CardTitle><CardDescription>{t("Live, with your settings.")}</CardDescription></CardHeader>
                <CardContent><Preview config={config} lang={lang} /></CardContent>
              </Card>
              <Card>
                <CardHeader><CardTitle>{t("Add it to your site")}</CardTitle></CardHeader>
                <CardContent className="space-y-3 text-sm">
                  <p className="text-muted-foreground">{t("1. Add this line to every page, in <head>:")}</p>
                  <div className="flex items-start gap-2 rounded-md border bg-muted/40 p-2.5"><code className="min-w-0 flex-1 break-all text-xs">{snippet}</code>
                    <Button variant="ghost" size="icon" className="size-7 shrink-0" aria-label={t("Copy")} onClick={() => { navigator.clipboard?.writeText(snippet); toast.success(t("Copied.")); }}><Copy /></Button></div>
                  <p className="text-muted-foreground">{t("2. Hold back anything that sets cookies until the visitor agrees:")}</p>
                  <div className="rounded-md border bg-muted/40 p-2.5"><code className="whitespace-pre-wrap break-all text-xs">{`<script type="text/plain" data-mas-consent="analytics" src="https://…"></script>\n<iframe data-mas-consent="marketing" data-src="https://www.youtube.com/embed/…"></iframe>`}</code></div>
                  <p className="text-muted-foreground">{t("3. A link to reopen the choices: data-mas-cookie-settings. Google Consent Mode is updated for you when gtag is present.")}</p>
                  <p className="text-xs text-muted-foreground">{t("The choice is kept in a cookie of your own site. Mas only counts the decisions, with no identifier.")}</p>
                </CardContent>
              </Card>
            </div>
          </div>
        </>
      )}
    </>
  );
}

export default function CookiesPage() {
  return <CmsLayout><Cookies /></CmsLayout>;
}
