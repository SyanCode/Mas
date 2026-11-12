"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, ExternalLink, Loader2, Trash2 } from "lucide-react";
import { CmsLayout, NoSite, PageTitle, useCms } from "@/components/cms/CmsLayout";
import { StatusBadge, parseUtc } from "@/components/cms/common";
import { emptyItem, FieldInput, type Previews, type Values } from "@/components/cms/fields";
import { PublishBanner, usePublisher, type Saved } from "@/components/cms/publish";
import { Button } from "@/components/cms/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/cms/ui/card";
import { ConfirmDialog } from "@/components/cms/ui/dialog";
import { Input } from "@/components/cms/ui/input";
import { Label } from "@/components/cms/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/cms/ui/select";
import { Skeleton } from "@/components/cms/ui/skeleton";
import { api, type ApiError, type CmsCollection, type CmsEntry, type CmsStatus } from "@/lib/api";
import { useT } from "@/lib/i18n";

/** "YYYY-MM-DDTHH:MM" UTC (stored) → value of a datetime-local input (local time). */
function toLocalInput(utc: string | null | undefined): string {
  if (!utc) return "";
  const d = parseUtc(utc);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
const toUtc = (local: string) => (local ? new Date(local).toISOString().slice(0, 16) : null);

function Editor() {
  const t = useT();
  const router = useRouter();
  const params = useSearchParams();
  const cid = params.get("c") ?? "";
  const slug = params.get("slug");
  const { site, reloadOverview } = useCms();
  const publisher = usePublisher(site);
  const [collection, setCollection] = useState<CmsCollection | null>(null);
  const [original, setOriginal] = useState<Values | null>(null);
  const [values, setValues] = useState<Values>({});
  const [sha, setSha] = useState<string | null>(null);
  const [status, setStatus] = useState<CmsStatus>("draft");
  const [origStatus, setOrigStatus] = useState<CmsStatus>("draft");
  const [when, setWhen] = useState("");
  const [address, setAddress] = useState("");
  const [note, setNote] = useState("");
  const [previews, setPreviews] = useState<Previews>({});
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState(false);

  const base = site ? `/cms/sites/${encodeURIComponent(site.id)}/collections/${encodeURIComponent(cid)}/entries` : "";

  const load = useCallback(async () => {
    if (!site) return;
    try {
      const s = await api<{ collections: CmsCollection[] }>(`/cms/sites/${encodeURIComponent(site.id)}`);
      const c = s.collections.find((x) => x.id === cid) ?? null;
      setCollection(c);
      if (!c) return setError(t("Unknown collection."));
      if (slug) {
        const e = await api<CmsEntry>(`${base}/${encodeURIComponent(slug)}`);
        setOriginal(e.values); setValues(e.values); setSha(e.sha);
        setStatus(e.status ?? "published"); setOrigStatus(e.status ?? "published"); setWhen(toLocalInput(e.publish_at));
      } else {
        const empty = emptyItem(c.fields);
        setOriginal(empty); setValues(empty); setSha(null);
        const first: CmsStatus = c.drafts ? "draft" : "published";
        setStatus(first); setOrigStatus(first); setWhen("");
      }
    } catch (e) { setError((e as ApiError).detail); }
  }, [site, cid, slug, base, t]);
  useEffect(() => { setOriginal(null); load(); }, [load]);

  const dirty = original !== null && (JSON.stringify(values) !== JSON.stringify(original) || status !== origStatus || (status === "scheduled" && when !== ""));
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (dirty) e.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const title = useMemo(() => (collection ? String(values[collection.title_field] ?? "") : ""), [collection, values]);
  const backHref = site ? `/cms/content/?app=${encodeURIComponent(site.id)}&c=${encodeURIComponent(cid)}` : "/cms/";

  async function save(next?: CmsStatus) {
    if (!collection) return;
    const st = next ?? status;
    const body = { values, note, status: collection.drafts ? st : undefined, publish_at: st === "scheduled" ? toUtc(when) : undefined };
    let created = slug;
    const ok = await publisher.run(async () => {
      const r = slug === null
        ? await api<Saved & { slug: string }>(base, { method: "POST", body: { ...body, slug: address.trim() || null } })
        : await api<Saved & { slug: string }>(`${base}/${encodeURIComponent(slug)}`, { method: "PUT", body: { ...body, sha } });
      created = r.slug;
      return r;
    }, st === "draft" && collection.drafts ? t("Draft saved.") : st === "scheduled" ? t("Scheduled.") : t("Published."));
    if (!ok) return;
    setNote(""); reloadOverview();
    if (slug === null && created) router.replace(`/cms/entry/?app=${encodeURIComponent(site!.id)}&c=${encodeURIComponent(cid)}&slug=${encodeURIComponent(created)}`);
    else load();
  }

  async function remove() {
    setDeleting(false);
    if (!slug) return;
    const ok = await publisher.run(() => api<Saved>(`${base}/${encodeURIComponent(slug)}?sha=${encodeURIComponent(sha ?? "")}`, { method: "DELETE" }), t("Deleted."));
    if (ok) { reloadOverview(); router.replace(backHref); }
  }

  if (!site) return <NoSite />;
  const busy = publisher.busy;
  const drafts = !!collection?.drafts;
  const mainLabel = !drafts ? t("Publish") : status === "draft" ? t("Save draft") : status === "scheduled" ? t("Schedule") : origStatus === "published" && slug ? t("Update") : t("Publish");
  const scheduleInvalid = status === "scheduled" && (!when || new Date(when).getTime() <= Date.now());

  return (
    <>
      <Link href={backHref} className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" />{collection?.label ?? t("Back to the list")}</Link>
      <PageTitle title={slug === null ? `${t("New")} ${collection?.item_label.toLowerCase() ?? ""}` : title || slug || "…"}
        actions={drafts && slug ? <StatusBadge status={origStatus} /> : undefined} />
      <PublishBanner state={publisher.state} site={site} />
      {(error || publisher.error) && <p className="mb-4 text-sm text-destructive">{error || publisher.error}</p>}

      {!collection || original === null ? <Skeleton className="h-96" /> : (
        <form onSubmit={(e) => { e.preventDefault(); save(); }} className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <Card><CardContent className="space-y-6 p-6">
            {collection.fields.map((f) => (
              <FieldInput key={f.key} appId={site.id} field={f} value={values[f.key]} previews={previews}
                addPreview={(k, url) => setPreviews((p) => ({ ...p, [k]: url }))}
                onChange={(v) => setValues((cur) => ({ ...cur, [f.key]: v }))} />
            ))}
          </CardContent></Card>

          <div className="space-y-4 lg:sticky lg:top-20 lg:self-start">
            <Card>
              <CardHeader className="pb-3"><CardTitle>{drafts ? t("Publish") : t("Save")}</CardTitle></CardHeader>
              <CardContent className="space-y-4">
                {drafts && (
                  <div className="space-y-1.5">
                    <Label>{t("Status")}</Label>
                    <Select value={status} onValueChange={(v) => setStatus(v as CmsStatus)}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="draft">{t("Draft")}</SelectItem>
                        <SelectItem value="published">{t("Published")}</SelectItem>
                        <SelectItem value="scheduled">{t("Scheduled")}</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                )}
                {drafts && status === "scheduled" && (
                  <div className="space-y-1.5">
                    <Label htmlFor="when">{t("Publish on")}</Label>
                    <Input id="when" type="datetime-local" value={when} min={toLocalInput(new Date().toISOString().slice(0, 16))} onChange={(e) => setWhen(e.target.value)} />
                    <p className="text-xs text-muted-foreground">{t("Published automatically at that time (your local time).")}</p>
                  </div>
                )}
                {slug === null ? (
                  <div className="space-y-1.5">
                    <Label htmlFor="address">{t("Address")}</Label>
                    <Input id="address" value={address} maxLength={80} placeholder="mon-article" onChange={(e) => setAddress(e.target.value.toLowerCase())} />
                    <p className="text-xs text-muted-foreground">{t("Leave empty to build it from the title. It cannot be changed once published.")}</p>
                  </div>
                ) : <p className="text-xs text-muted-foreground">{t("Address")}: <code>{slug}</code></p>}
                <div className="space-y-1.5">
                  <Label htmlFor="note">{t("Note")}</Label>
                  <Input id="note" value={note} maxLength={100} placeholder={t("What did you change? (optional)")} onChange={(e) => setNote(e.target.value)} />
                </div>
                <div className="flex flex-col gap-2 pt-1">
                  <Button type="submit" disabled={busy || (!dirty && slug !== null) || scheduleInvalid}>{busy && <Loader2 className="animate-spin" />}{mainLabel}</Button>
                  {dirty && slug !== null && <Button type="button" variant="ghost" onClick={() => { setValues(original); setStatus(origStatus); }}>{t("Discard")}</Button>}
                  {slug !== null && <Button type="button" variant="ghost" className="text-destructive hover:text-destructive" disabled={busy} onClick={() => setDeleting(true)}><Trash2 />{t("Delete")}</Button>}
                </div>
              </CardContent>
            </Card>
            {site.url && slug && (!drafts || origStatus === "published") && (
              <a href={site.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"><ExternalLink className="size-3.5" />{t("View site")}</a>
            )}
          </div>
        </form>
      )}
      <ConfirmDialog open={deleting} destructive title={t("Delete “{name}”?", { name: title || slug || "" })}
        description={t("It is removed from the site at the next publication. Your administrator can bring it back from the history.")}
        confirmLabel={t("Delete")} cancelLabel={t("Cancel")} onConfirm={remove} onCancel={() => setDeleting(false)} />
    </>
  );
}

export default function EntryPage() {
  return <CmsLayout><Editor /></CmsLayout>;
}
