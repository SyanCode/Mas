"use client";

import { useEffect, useState } from "react";
import { Loader2, Mail, Send, Webhook } from "lucide-react";
import { toast } from "sonner";
import { CmsLayout, NoSite, PageTitle, useCms } from "@/components/cms/CmsLayout";
import { Button } from "@/components/cms/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/cms/ui/card";
import { Input } from "@/components/cms/ui/input";
import { FormItem } from "@/components/cms/ui/form-item";
import { Label } from "@/components/cms/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/cms/ui/select";
import { Skeleton } from "@/components/cms/ui/skeleton";
import { Switch } from "@/components/cms/ui/switch";
import { api, type ApiError } from "@/lib/api";
import { msg, useT } from "@/lib/i18n";

interface Smtp { host: string; port: number; security: string; user: string; from: string; has_password: boolean }
interface SiteNotify { emails: string[]; has_webhook: boolean; events: string[]; digest: boolean; all_events: string[] }

const EVENT_LABEL: Record<string, string> = {
  compliance: msg("The compliance audit got worse"),
  deploy_failed: msg("A deployment failed"),
  scheduled: msg("A scheduled entry went live"),
  uptime: msg("The site went down or came back"),
};

function Notifications() {
  const t = useT();
  const { site, session } = useCms();
  const [smtp, setSmtp] = useState<Smtp | null>(null);
  const [password, setPassword] = useState("");
  const [cfg, setCfg] = useState<SiteNotify | null>(null);
  const [emails, setEmails] = useState("");
  const [webhook, setWebhook] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  useEffect(() => { api<{ smtp: Smtp }>("/notify").then((r) => setSmtp(r.smtp)).catch((e: ApiError) => setError(e.detail)); }, []);
  useEffect(() => {
    if (!site) return;
    api<SiteNotify>(`/cms/sites/${encodeURIComponent(site.id)}/notify`).then((r) => { setCfg(r); setEmails(r.emails.join(", ")); setWebhook(""); }).catch((e: ApiError) => setError(e.detail));
  }, [site]);

  if (session.role !== "admin") return <p className="text-sm text-muted-foreground">{t("Only administrators manage notifications.")}</p>;
  if (!site) return <NoSite />;

  async function run(name: string, fn: () => Promise<void>) {
    setBusy(name); setError("");
    try { await fn(); } catch (e) { setError((e as ApiError).detail); toast.error((e as ApiError).detail); } finally { setBusy(""); }
  }
  const base = `/cms/sites/${encodeURIComponent(site.id)}/notify`;

  return (
    <>
      <PageTitle title={t("Notifications")} description={t("Tell the right people when something needs attention, by e-mail or webhook (Slack, Discord…).")} />
      {error && <p className="mb-4 text-sm text-destructive">{error}</p>}
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2"><Mail className="size-4" />{t("Mail server")}</CardTitle><CardDescription>{t("Used for every site. The password is stored encrypted.")}</CardDescription></CardHeader>
          <CardContent className="space-y-4">
            {!smtp ? <Skeleton className="h-48" /> : (
              <>
                <div className="grid grid-cols-3 gap-3">
                  <FormItem className="col-span-2 space-y-1.5"><Label>{t("Server")}</Label><Input value={smtp.host} onChange={(e) => setSmtp({ ...smtp, host: e.target.value })} placeholder="smtp.example.com" /></FormItem>
                  <FormItem className="space-y-1.5"><Label>{t("Port")}</Label><Input type="number" value={smtp.port} onChange={(e) => setSmtp({ ...smtp, port: Number(e.target.value) })} /></FormItem>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <FormItem className="space-y-1.5"><Label>{t("Security")}</Label>
                    <Select value={smtp.security} onValueChange={(v) => setSmtp({ ...smtp, security: v })}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent><SelectItem value="starttls">STARTTLS (587)</SelectItem><SelectItem value="ssl">SSL/TLS (465)</SelectItem><SelectItem value="none">{t("None")} (25)</SelectItem></SelectContent>
                    </Select></FormItem>
                  <FormItem className="space-y-1.5"><Label>{t("Sender address")}</Label><Input value={smtp.from} onChange={(e) => setSmtp({ ...smtp, from: e.target.value })} placeholder="noreply@example.com" /></FormItem>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <FormItem className="space-y-1.5"><Label>{t("Username")}</Label><Input value={smtp.user} onChange={(e) => setSmtp({ ...smtp, user: e.target.value })} /></FormItem>
                  <FormItem className="space-y-1.5"><Label>{t("Password")}</Label><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder={smtp.has_password ? "••••••••" : ""} /></FormItem>
                </div>
                <Button disabled={busy === "smtp"} onClick={() => run("smtp", async () => { setSmtp(await api<Smtp>("/notify/smtp", { method: "PUT", body: { ...smtp, password } })); setPassword(""); toast.success(t("Saved.")); })}>
                  {busy === "smtp" && <Loader2 className="animate-spin" />}{t("Save")}
                </Button>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2"><Webhook className="size-4" />{site.name}</CardTitle><CardDescription>{t("Who is told, and about what, for this site.")}</CardDescription></CardHeader>
          <CardContent className="space-y-4">
            {!cfg ? <Skeleton className="h-48" /> : (
              <>
                <FormItem className="space-y-1.5"><Label>{t("E-mail addresses")}</Label><Input value={emails} onChange={(e) => setEmails(e.target.value)} placeholder="client@example.com, me@example.com" /></FormItem>
                <FormItem className="space-y-1.5"><Label>{t("Webhook")}</Label><Input value={webhook} onChange={(e) => setWebhook(e.target.value)} placeholder={cfg.has_webhook ? t("A webhook is saved (type a new address to replace it)") : "https://hooks.slack.com/services/…"} />
                  <p className="text-xs text-muted-foreground">{t("Slack, Discord or any address that accepts JSON. https only. Stored encrypted.")}</p></FormItem>
                <div className="space-y-2.5">
                  <Label>{t("Tell me when")}</Label>
                  {Object.keys(EVENT_LABEL).map((ev) => (
                    <div key={ev} className="flex items-center gap-3"><Switch id={`ev-${ev}`} checked={cfg.events.includes(ev)}
                      onCheckedChange={(on) => setCfg({ ...cfg, events: on ? [...cfg.events, ev] : cfg.events.filter((x) => x !== ev) })} /><Label htmlFor={`ev-${ev}`} className="font-normal">{t(EVENT_LABEL[ev])}</Label></div>
                  ))}
                  <div className="flex items-center gap-3"><Switch id="digest" checked={cfg.digest} onCheckedChange={(on) => setCfg({ ...cfg, digest: on })} /><Label htmlFor="digest" className="font-normal">{t("Send a weekly report on Monday morning")}</Label></div>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button disabled={busy === "site"} onClick={() => run("site", async () => {
                    const r = await api<SiteNotify>(base, { method: "PUT", body: { emails: emails.split(",").map((x) => x.trim()).filter(Boolean), webhook, events: cfg.events, digest: cfg.digest } });
                    setCfg(r); setWebhook(""); toast.success(t("Saved."));
                  })}>{busy === "site" && <Loader2 className="animate-spin" />}{t("Save")}</Button>
                  <Button variant="outline" disabled={busy === "test"} onClick={() => run("test", async () => { await api(`${base}/test`, { method: "POST" }); toast.success(t("Test sent.")); })}>{busy === "test" ? <Loader2 className="animate-spin" /> : <Send />}{t("Send a test")}</Button>
                  <Button variant="outline" disabled={busy === "digest"} onClick={() => run("digest", async () => { const r = await api<{ sent: boolean }>(`${base}/digest`, { method: "POST" }); toast.success(r.sent ? t("Report sent.") : t("No e-mail address: nothing sent.")); })}>{t("Send the weekly report now")}</Button>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}

export default function NotificationsPage() {
  return <CmsLayout><Notifications /></CmsLayout>;
}
