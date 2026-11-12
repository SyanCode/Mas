"use client";

import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import { Copy, Download, Loader2, Upload } from "lucide-react";
import { toast } from "sonner";
import { CmsLayout, NoSite, PageTitle, useCms } from "@/components/cms/CmsLayout";
import { Badge } from "@/components/cms/ui/badge";
import { Button } from "@/components/cms/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/cms/ui/card";
import { Input } from "@/components/cms/ui/input";
import { FormItem } from "@/components/cms/ui/form-item";
import { Label } from "@/components/cms/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/cms/ui/select";
import { Skeleton } from "@/components/cms/ui/skeleton";
import { Switch } from "@/components/cms/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/cms/ui/tabs";
import { api, type ApiError } from "@/lib/api";
import { msg, useT } from "@/lib/i18n";

type Profile = Record<string, string>;
interface Doc { id: string; title: string; filename: string; markdown: string; missing: string[] }
interface Payload { profile: Profile; detected: { services: { name: string; category: string; region: string }[] }; audited: boolean; documents: Doc[] }

const FIELDS: [string, string][] = [
  ["company", msg("Company or publisher name")], ["legal_form", msg("Legal form (SARL, SAS, auto-entrepreneur…)")], ["address", msg("Address")],
  ["siret", msg("SIRET / registration number")], ["vat", msg("VAT number")], ["capital", msg("Share capital")], ["rcs", msg("RCS city")],
  ["email", msg("Contact e-mail")], ["phone", msg("Phone")], ["director", msg("Publication director")],
  ["host_name", msg("Host name")], ["host_address", msg("Host address")], ["host_phone", msg("Host phone")],
  ["dpo", msg("Data protection contact (optional)")], ["retention_months", msg("Keep contact messages (months)")],
  ["extra_services", msg("Other services used (comma separated)")],
];
const LABEL: Record<string, string> = Object.fromEntries(FIELDS);

function Legal() {
  const t = useT();
  const { site } = useCms();
  const [lang, setLang] = useState("fr");
  const [data, setData] = useState<Payload | null>(null);
  const [profile, setProfile] = useState<Profile>({});
  const [folder, setFolder] = useState("content/legal");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const base = site ? `/cms/sites/${encodeURIComponent(site.id)}/legal` : "";

  const apply = (r: Payload) => { setData(r); setProfile(r.profile); };
  useEffect(() => { if (site) api<Payload>(`${base}?lang=${lang}`).then(apply).catch((e: ApiError) => setError(e.detail)); }, [site, base, lang]);

  if (!site) return <NoSite />;
  async function run(name: string, fn: () => Promise<void>) {
    setBusy(name); setError("");
    try { await fn(); } catch (e) { setError((e as ApiError).detail); toast.error((e as ApiError).detail); } finally { setBusy(""); }
  }
  const download = (d: Doc) => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([d.markdown], { type: "text/markdown" })); a.download = d.filename; a.click();
  };

  return (
    <>
      <PageTitle title={t("Legal pages")} description={t("Drafts of your legal notice, privacy policy and cookie policy, filled from your details and the services Mas found on the site.")}
        actions={<Select value={lang} onValueChange={setLang}><SelectTrigger className="w-36"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="fr">Français</SelectItem><SelectItem value="en">English</SelectItem></SelectContent></Select>} />
      {error && <p className="mb-4 text-sm text-destructive">{error}</p>}
      {!data ? <Skeleton className="h-96" /> : (
        <div className="grid gap-6 lg:grid-cols-5">
          <Card className="lg:col-span-2">
            <CardHeader><CardTitle>{t("Your details")}</CardTitle><CardDescription>{t("Empty fields stay marked [TO COMPLETE] in the pages.")}</CardDescription></CardHeader>
            <CardContent className="space-y-3.5">
              {FIELDS.map(([k, label]) => (
                <FormItem key={k} className="space-y-1"><Label className="text-xs">{t(label)}</Label><Input value={profile[k] ?? ""} onChange={(e) => setProfile({ ...profile, [k]: e.target.value })} /></FormItem>
              ))}
              <div className="flex items-center gap-3"><Switch id="nl" checked={profile.newsletter === "true"} onCheckedChange={(on) => setProfile({ ...profile, newsletter: on ? "true" : "" })} /><Label htmlFor="nl" className="font-normal">{t("The site sends a newsletter")}</Label></div>
              <div className="rounded-md border bg-muted/40 p-3 text-xs text-muted-foreground">
                {data.audited ? (data.detected.services.length ? <>{t("Services found by the audit")}: {data.detected.services.map((s) => s.name).join(", ")}</> : t("The audit found no third-party service."))
                  : t("Run an audit first so the cookie policy lists the services of the site.")}
              </div>
              <Button disabled={busy === "save"} onClick={() => run("save", async () => { apply(await api<Payload>(`${base}?lang=${lang}`, { method: "PUT", body: profile })); toast.success(t("Saved.")); })}>
                {busy === "save" && <Loader2 className="animate-spin" />}{t("Save and update the pages")}
              </Button>
            </CardContent>
          </Card>

          <div className="space-y-4 lg:col-span-3">
            <Tabs defaultValue="legal">
              <TabsList>{data.documents.map((d) => <TabsTrigger key={d.id} value={d.id}>{d.title}{d.missing.length > 0 && <Badge variant="warning" className="ml-2">{d.missing.length}</Badge>}</TabsTrigger>)}</TabsList>
              {data.documents.map((d) => (
                <TabsContent key={d.id} value={d.id}>
                  <Card><CardContent className="space-y-4 p-5">
                    {d.missing.length > 0 && <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">{t("To complete")}: {d.missing.map((m) => t(LABEL[m] ?? m)).join(", ")}</p>}
                    <div className="max-h-[32rem] overflow-auto text-sm leading-relaxed [&_a]:text-primary [&_a]:underline [&_h1]:mb-3 [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:mb-2 [&_h2]:mt-5 [&_h2]:text-base [&_h2]:font-semibold [&_li]:ml-5 [&_li]:list-disc [&_p]:mb-3 [&_table]:my-3 [&_table]:w-full [&_td]:border [&_td]:p-2 [&_th]:border [&_th]:p-2 [&_th]:text-left">
                      <Markdown>{d.markdown.replace(/<!--.*?-->/s, "")}</Markdown>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button variant="outline" size="sm" onClick={() => { navigator.clipboard?.writeText(d.markdown); toast.success(t("Copied.")); }}><Copy />{t("Copy Markdown")}</Button>
                      <Button variant="outline" size="sm" onClick={() => download(d)}><Download />{t("Download")}</Button>
                    </div>
                  </CardContent></Card>
                </TabsContent>
              ))}
            </Tabs>
            <Card>
              <CardHeader><CardTitle>{t("Add them to the site")}</CardTitle><CardDescription>{t("Writes the three pages as Markdown files in your repository (one commit). Your site then displays them like any other page.")}</CardDescription></CardHeader>
              <CardContent className="flex flex-wrap items-end gap-3">
                <FormItem className="space-y-1"><Label className="text-xs">{t("Folder")}</Label><Input value={folder} onChange={(e) => setFolder(e.target.value)} className="w-56" /></FormItem>
                <Button disabled={busy === "pub"} onClick={() => run("pub", async () => { await api(`${base}/publish`, { method: "POST", body: { lang, folder } }); toast.success(t("Pages written. The site is being rebuilt.")); })}>
                  {busy === "pub" ? <Loader2 className="animate-spin" /> : <Upload />}{t("Write to the repository")}
                </Button>
              </CardContent>
            </Card>
            <p className="text-xs text-muted-foreground">{t("Drafts to read, complete and have checked. They are not legal advice.")}</p>
          </div>
        </div>
      )}
    </>
  );
}

export default function LegalPage() {
  return <CmsLayout><Legal /></CmsLayout>;
}
