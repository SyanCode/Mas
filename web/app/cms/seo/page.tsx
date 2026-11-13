"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Copy, Info, Loader2, Plus, ScanSearch, Sparkles, Trash2, Wand2, XCircle } from "lucide-react";
import { toast } from "sonner";
import { CmsLayout, NoSite, PageTitle, useCms } from "@/components/cms/CmsLayout";
import { Badge } from "@/components/cms/ui/badge";
import { Button } from "@/components/cms/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/cms/ui/card";
import { Input, Textarea } from "@/components/cms/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/cms/ui/select";
import { Skeleton } from "@/components/cms/ui/skeleton";
import { Switch } from "@/components/cms/ui/switch";
import { ClaudeFix } from "@/components/cms/ClaudeFix";
import { api, type ApiError, type SeoAudit, type SeoIssue, type SeoState, type SiteRulesConfig } from "@/lib/api";
import { useT } from "@/lib/i18n";

const ICON = { error: <XCircle className="size-4 text-red-500" />, warn: <AlertTriangle className="size-4 text-amber-500" />, info: <Info className="size-4 text-sky-500" /> };

const FIXABLE: Record<string, "robots" | "sitemap"> = { "robots-missing": "robots", "robots-blocks": "robots", "sitemap-missing": "sitemap", "sitemap-unlisted": "sitemap" };

function Issues({ items, onFix, busy }: { items: SeoIssue[]; onFix?: (fix: "robots" | "sitemap") => void; busy?: boolean }) {
  const t = useT();
  return (
    <ul className="space-y-2">
      {items.map((i, n) => (
        <li key={`${i.id}${n}`} className="flex gap-2.5 text-sm">
          <span className="mt-0.5 shrink-0">{ICON[i.level]}</span>
          <span><span className="font-medium">{t(i.title, i.vars)}</span>
            {i.fix && <span className="block text-muted-foreground">{t(i.fix, i.vars)}</span>}
            {onFix && FIXABLE[i.id] && <Button size="sm" variant="outline" className="mt-1.5" disabled={busy} onClick={() => onFix(FIXABLE[i.id])}><Wand2 />{t("Fix with Mas")}</Button>}</span>
        </li>
      ))}
    </ul>
  );
}

function Seo() {
  const t = useT();
  const { site, session } = useCms();
  const [state, setState] = useState<SeoState | null>(null);
  const [cfg, setCfg] = useState<SiteRulesConfig | null>(null);
  const [audit, setAudit] = useState<SeoAudit | null>(null);
  const [busy, setBusy] = useState("");
  const base = site ? `/cms/sites/${encodeURIComponent(site.id)}/seo` : "";
  const admin = session.role === "admin";

  const load = useCallback(() => {
    if (!base) return;
    api<SeoState>(base).then((s) => {
      setState(s); setAudit(s.audit);
      const r = s.rules;
      setCfg({ redirects: r?.redirects ?? [], robots: r?.robots ?? { enabled: false, text: "" }, sitemap: r?.sitemap ?? { enabled: false, pages: ["/"], collections: {} } });
    }).catch((e: ApiError) => toast.error(e.detail));
  }, [base]);
  useEffect(() => { setState(null); load(); }, [load]);

  if (!site) return <NoSite />;
  if (!state || !cfg) return <><PageTitle title={t("Search engines")} /><Skeleton className="h-64" /></>;

  const run = async () => {
    setBusy("audit");
    try { setAudit(await api<SeoAudit>(`${base}/audit`, { method: "POST" })); } catch (e) { toast.error((e as ApiError).detail); } finally { setBusy(""); }
  };
  const save = async () => {
    setBusy("save");
    try { await api(`${base}/rules`, { method: "PUT", body: cfg }); toast.success(t("Applied to the site.")); load(); } catch (e) { toast.error((e as ApiError).detail); } finally { setBusy(""); }
  };
  const supported = state.rules?.supported ?? false;
  const fixable = audit ? [...new Set(audit.site.map((i) => FIXABLE[i.id]).filter(Boolean))] : [];
  const optimize = async (fixes: ("robots" | "sitemap")[]) => {
    setBusy("fix");
    try {
      await api(`${base}/optimize`, { method: "POST", body: { fixes } });
      toast.success(t("Done: robots.txt and the sitemap are in place."));
      load();
      setAudit(await api<SeoAudit>(`${base}/audit`, { method: "POST" }));
    } catch (e) { toast.error((e as ApiError).detail); } finally { setBusy(""); }
  };
  const copy = (text: string) => { navigator.clipboard?.writeText(text).catch(() => undefined); toast.success(t("Copied.")); };
  const setRedirect = (i: number, patch: Partial<SiteRulesConfig["redirects"][number]>) => setCfg({ ...cfg, redirects: cfg.redirects.map((r, n) => (n === i ? { ...r, ...patch } : r)) });
  const level = (n: number) => (n >= 90 ? "text-emerald-500" : n >= 70 ? "text-amber-500" : "text-red-500");

  return (
    <>
      <PageTitle title={t("Search engines")} description={t("How the site looks to Google and others, and the files that guide them.")}
        actions={state.domain ? <Button onClick={run} disabled={!!busy}>{busy === "audit" ? <Loader2 className="animate-spin" /> : <ScanSearch />}{t("Check the site")}</Button> : undefined} />

      {!state.domain && <Card className="p-8 text-center text-sm text-muted-foreground">{t("Give the site a domain first: the check reads the live site.")}</Card>}

      {state.domain && <ClaudeFix preset="seo" title={t("Improve the SEO with Claude")} description={t("Claude reads the site and its pages, fixes titles, descriptions, headings, robots.txt and the sitemap, deploys and checks again.")} onDone={() => { load(); run(); }} />}

      {audit && (
        <Card className="mb-6">
          <CardHeader className="flex-row flex-wrap items-center gap-x-5 gap-y-2 space-y-0">
            <div className={`font-light tabular-nums text-5xl ${level(audit.score)}`}>{audit.score}</div>
            <div>
              <CardTitle>{audit.reachable ? t("Search readiness") : t("The site could not be reached")}</CardTitle>
              {audit.counts && <CardDescription>{audit.counts.error} {t("errors")} · {audit.counts.warn} {t("warnings")} · {audit.counts.info} {t("tips")}</CardDescription>}
            </div>
          </CardHeader>
          <CardContent className="space-y-6">
            {admin && supported && fixable.length > 0 && (
              <div className="flex flex-wrap items-center gap-3 rounded-xl bg-brand/[0.07] p-4">
                <Sparkles className="size-4 text-brand" />
                <p className="min-w-[14rem] flex-1 basis-56 text-sm">{t("Mas can set up robots.txt and the sitemap for you in one step.")}</p>
                <Button onClick={() => optimize(["robots", "sitemap"])} disabled={!!busy}>{busy === "fix" ? <Loader2 className="animate-spin" /> : <Wand2 />}{t("Optimize now")}</Button>
              </div>
            )}
            {audit.site.length > 0 && <div><h3 className="mb-2 text-sm font-medium">{t("The whole site")}</h3><Issues items={audit.site} busy={!!busy} onFix={admin && supported ? (f) => optimize([f]) : undefined} /></div>}
            {audit.pages.map((p) => (
              <div key={p.url} className="rounded-xl bg-muted/50 p-4">
                <div className="mb-3 text-xs text-muted-foreground">{p.url.replace(/^https?:\/\//, "")}</div>
                {p.title && (
                  <div className="mb-3 rounded-lg bg-card p-3 shadow-[0_0_0_1px_var(--border)]">
                    <div className="truncate text-[17px] text-[#1a0dab] dark:text-sky-400">{p.title}</div>
                    <div className="line-clamp-2 text-sm text-muted-foreground">{p.description || t("No description: the engine picks a sentence of the page.")}</div>
                  </div>
                )}
                {(p.suggestions?.title || p.suggestions?.description) && (
                  <div className="mb-3 space-y-2 rounded-lg border border-dashed border-brand/40 p-3 text-sm">
                    <div className="flex items-center gap-1.5 text-xs font-medium text-brand"><Sparkles className="size-3.5" />{t("Suggested wording")}</div>
                    {(["title", "description"] as const).map((k) => p.suggestions?.[k] && (
                      <div key={k} className="flex items-start gap-2">
                        <div className="min-w-0 flex-1"><div className="text-xs uppercase text-muted-foreground">{k === "title" ? t("Title") : t("Description")} · {p.suggestions[k]!.length}</div><div>{p.suggestions[k]}</div></div>
                        <Button variant="ghost" size="icon" aria-label={t("Copy")} onClick={() => copy(p.suggestions![k]!)}><Copy /></Button>
                      </div>
                    ))}
                  </div>
                )}
                {p.issues.length === 0 ? <p className="flex items-center gap-2 text-sm text-emerald-600"><CheckCircle2 className="size-4" />{t("Nothing to fix.")}</p> : <Issues items={p.issues} />}
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {!supported && state.domain && <Card className="mb-6 p-5 text-sm text-muted-foreground">{t("Redirects, robots.txt and the sitemap are written by Mas into the nginx site of apps it deploys. This site is served another way.")}</Card>}

      {supported && (
        <>
          <Card className="mb-6">
            <CardHeader><CardTitle>{t("Redirects")}</CardTitle><CardDescription>{t("Send an old address to a new one. 301 tells search engines the move is permanent. /blog/* moves a whole section.")}</CardDescription></CardHeader>
            <CardContent className="space-y-2">
              {cfg.redirects.length === 0 && <p className="py-2 text-sm text-muted-foreground">{t("No redirect.")}</p>}
              {cfg.redirects.map((r, i) => (
                <div key={i} className="flex items-center gap-2">
                  <Input value={r.from} disabled={!admin} placeholder="/old-page" aria-label={t("From")} onChange={(e) => setRedirect(i, { from: e.target.value })} className="font-mono text-[13px]" />
                  <span className="text-muted-foreground">→</span>
                  <Input value={r.to} disabled={!admin} placeholder="/new-page" aria-label={t("To")} onChange={(e) => setRedirect(i, { to: e.target.value })} className="font-mono text-[13px]" />
                  <Select value={String(r.code)} disabled={!admin} onValueChange={(v) => setRedirect(i, { code: Number(v) as 301 | 302 })}>
                    <SelectTrigger className="w-28" aria-label={t("Type")}><SelectValue /></SelectTrigger>
                    <SelectContent><SelectItem value="301">301 · {t("permanent")}</SelectItem><SelectItem value="302">302 · {t("temporary")}</SelectItem></SelectContent>
                  </Select>
                  {admin && <Button variant="ghost" size="icon" aria-label={t("Delete")} onClick={() => setCfg({ ...cfg, redirects: cfg.redirects.filter((_, n) => n !== i) })}><Trash2 /></Button>}
                </div>
              ))}
              {admin && <Button variant="outline" size="sm" onClick={() => setCfg({ ...cfg, redirects: [...cfg.redirects, { from: "", to: "", code: 301 }] })}><Plus />{t("Add a redirect")}</Button>}
            </CardContent>
          </Card>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader className="flex-row items-start justify-between space-y-0">
                <div><CardTitle>robots.txt</CardTitle><CardDescription>{t("Tells search engines what they may visit. Replaces the file of the repository.")}</CardDescription></div>
                <Switch checked={cfg.robots.enabled} disabled={!admin} aria-label="robots.txt" onCheckedChange={(on) => setCfg({ ...cfg, robots: { ...cfg.robots, enabled: on } })} />
              </CardHeader>
              {cfg.robots.enabled && <CardContent><Textarea rows={7} disabled={!admin} className="font-mono text-[13px]" value={cfg.robots.text} placeholder={state.rules?.default_robots} onChange={(e) => setCfg({ ...cfg, robots: { ...cfg.robots, text: e.target.value } })} /></CardContent>}
            </Card>
            <Card>
              <CardHeader className="flex-row items-start justify-between space-y-0">
                <div><CardTitle>sitemap.xml</CardTitle><CardDescription>{t("The list of your pages for search engines. Published entries are added by themselves.")}</CardDescription></div>
                <Switch checked={cfg.sitemap.enabled} disabled={!admin} aria-label="sitemap.xml" onCheckedChange={(on) => setCfg({ ...cfg, sitemap: { ...cfg.sitemap, enabled: on } })} />
              </CardHeader>
              {cfg.sitemap.enabled && (
                <CardContent className="space-y-4">
                  <div className="space-y-1.5"><label className="text-sm font-medium">{t("Pages (one address per line)")}</label>
                    <Textarea rows={4} disabled={!admin} className="font-mono text-[13px]" value={cfg.sitemap.pages.join("\n")} onChange={(e) => setCfg({ ...cfg, sitemap: { ...cfg.sitemap, pages: e.target.value.split("\n").map((x) => x.trim()).filter(Boolean) } })} /></div>
                  {state.collections.map((c) => (
                    <div key={c.id} className="space-y-1.5"><label className="text-sm font-medium">{c.label}</label>
                      <Input disabled={!admin} className="font-mono text-[13px]" placeholder="/blog/{slug}" value={cfg.sitemap.collections[c.id] ?? ""}
                        onChange={(e) => { const next = { ...cfg.sitemap.collections }; if (e.target.value) next[c.id] = e.target.value; else delete next[c.id]; setCfg({ ...cfg, sitemap: { ...cfg.sitemap, collections: next } }); }} />
                      <p className="text-xs text-muted-foreground">{t("Address of an entry on your site, {slug} stands for its name. Empty: not listed.")}</p></div>
                  ))}
                </CardContent>
              )}
            </Card>
          </div>
          {admin && <div className="mt-6 flex justify-end"><Button onClick={save} disabled={!!busy}>{busy === "save" && <Loader2 className="animate-spin" />}{t("Apply to the site")}</Button></div>}
        </>
      )}
    </>
  );
}

export default function SeoPage() {
  return <CmsLayout><Seo /></CmsLayout>;
}
