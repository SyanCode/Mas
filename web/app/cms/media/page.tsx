"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Copy, ImagePlus, Loader2, Search, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import { CmsLayout, NoSite, PageTitle, useCms } from "@/components/cms/CmsLayout";
import { imageUrl, prepareImage } from "@/components/cms/fields";
import { PublishBanner, usePublisher, type Saved } from "@/components/cms/publish";
import { Badge } from "@/components/cms/ui/badge";
import { Button } from "@/components/cms/ui/button";
import { Card } from "@/components/cms/ui/card";
import { ConfirmDialog, Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/cms/ui/dialog";
import { Input } from "@/components/cms/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/cms/ui/select";
import { Skeleton } from "@/components/cms/ui/skeleton";
import { api, uploadFile, type ApiError, type CmsMedia } from "@/lib/api";
import { useT } from "@/lib/i18n";

type Item = CmsMedia["items"][number];
const kb = (n: number | null) => (n == null ? "" : n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

function Library() {
  const t = useT();
  const { site } = useCms();
  const publisher = usePublisher(site);
  const input = useRef<HTMLInputElement>(null);
  const [media, setMedia] = useState<CmsMedia | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [folder, setFolder] = useState("");
  const [open, setOpen] = useState<Item | null>(null);
  const [removing, setRemoving] = useState<Item | null>(null);
  const [uploading, setUploading] = useState(false);
  const base = site ? `/cms/sites/${encodeURIComponent(site.id)}/media` : "";

  const load = useCallback(() => {
    if (!site) return;
    api<CmsMedia>(base).then((m) => { setMedia(m); setFolder((f) => f || m.folders[0] || ""); }).catch((e: ApiError) => setError(e.detail));
  }, [site, base]);
  useEffect(() => { setMedia(null); load(); }, [load]);

  const items = useMemo(() => (media?.items ?? []).filter((i) => i.name.toLowerCase().includes(query.toLowerCase())), [media, query]);

  async function upload(files: FileList | null) {
    if (!files?.length || !site || !folder) return;
    setUploading(true);
    for (const file of Array.from(files)) {
      await publisher.run(async () => {
        const blob = await prepareImage(file, media?.max_kb ?? 2048);
        return uploadFile<Saved>(`${base}?folder=${encodeURIComponent(folder)}&name=${encodeURIComponent(file.name)}`, blob);
      }, t("Uploaded."));
    }
    setUploading(false);
    if (input.current) input.current.value = "";
    load();
  }

  async function remove(item: Item) {
    setRemoving(null); setOpen(null);
    const ok = await publisher.run(() => api<Saved>(`${base}/delete`, { method: "POST", body: { value: item.value } }), t("Deleted."));
    if (ok) load();
  }

  if (!site) return <NoSite />;
  return (
    <>
      <PageTitle title={t("Media")} description={t("Images of your site. Every upload is a commit; the site is rebuilt.")}
        actions={media && media.folders.length > 0 && (
          <>
            {media.folders.length > 1 && (
              <Select value={folder} onValueChange={setFolder}>
                <SelectTrigger className="w-48"><SelectValue /></SelectTrigger>
                <SelectContent>{media.folders.map((f) => <SelectItem key={f} value={f}>{f}</SelectItem>)}</SelectContent>
              </Select>
            )}
            <Button onClick={() => input.current?.click()} disabled={uploading}>{uploading ? <Loader2 className="animate-spin" /> : <Upload />}{t("Upload")}</Button>
            <input ref={input} type="file" multiple accept="image/png,image/jpeg,image/webp,image/gif,image/avif" className="hidden" onChange={(e) => upload(e.target.files)} />
          </>
        )} />
      <PublishBanner state={publisher.state} site={site} />
      {(error || publisher.error) && <p className="mb-4 text-sm text-destructive">{error || publisher.error}</p>}

      {!media ? <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-6">{Array.from({ length: 12 }, (_, i) => <Skeleton key={i} className="aspect-square" />)}</div> : media.folders.length === 0 ? (
        <Card className="p-10 text-center text-sm text-muted-foreground">{t("This site has no image field yet, so there is no media folder to manage.")}</Card>
      ) : (
        <>
          <div className="relative mb-4 w-full sm:w-72">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
            <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("Search…")} className="pl-8" />
          </div>
          {items.length === 0 ? (
            <Card className="flex flex-col items-center gap-2 p-12 text-center text-sm text-muted-foreground"><ImagePlus className="size-8" />{t("No image yet.")}</Card>
          ) : (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-6">
              {items.map((i) => (
                <button key={i.value} onClick={() => setOpen(i)} className="group overflow-hidden rounded-lg border bg-card text-left shadow-sm transition hover:ring-2 hover:ring-ring">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={imageUrl(site.id, i.value)} alt="" loading="lazy" className="aspect-square w-full bg-muted object-cover" />
                  <div className="space-y-0.5 p-2.5"><p className="truncate text-xs font-medium">{i.name}</p><p className="text-xs text-muted-foreground">{kb(i.size)}</p></div>
                </button>
              ))}
            </div>
          )}
          {media.supabase && <p className="mt-4 text-xs text-muted-foreground">{t("Images of Supabase collections are stored in Supabase Storage and are not listed here.")}</p>}
        </>
      )}

      <Dialog open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        <DialogContent className="max-w-2xl">
          {open && site && (
            <>
              <DialogHeader><DialogTitle className="break-all pr-6">{open.name}</DialogTitle><DialogDescription>{open.dir} · {kb(open.size)}</DialogDescription></DialogHeader>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={imageUrl(site.id, open.value)} alt="" className="max-h-80 w-full rounded-md bg-muted object-contain" />
              <div className="space-y-2 text-sm">
                <div className="flex items-center gap-2"><code className="min-w-0 flex-1 truncate rounded bg-muted px-2 py-1.5 text-xs">{open.value}</code>
                  <Button variant="outline" size="icon" aria-label={t("Copy")} onClick={() => { navigator.clipboard?.writeText(open.value); toast.success(t("Copied.")); }}><Copy /></Button></div>
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  {open.used_in.length ? <>{t("Used in")} {open.used_in.map((u) => <Badge key={u} variant="secondary">{u}</Badge>)}</> : t("Not used anywhere.")}
                </div>
              </div>
              <div className="flex justify-end"><Button variant="destructive" disabled={open.used_in.length > 0} onClick={() => setRemoving(open)}><Trash2 />{t("Delete")}</Button></div>
            </>
          )}
        </DialogContent>
      </Dialog>
      <ConfirmDialog open={!!removing} destructive title={t("Delete “{name}”?", { name: removing?.name ?? "" })}
        description={t("It is removed from the site at the next publication. Your administrator can bring it back from the history.")}
        confirmLabel={t("Delete")} cancelLabel={t("Cancel")} onConfirm={() => removing && remove(removing)} onCancel={() => setRemoving(null)} />
    </>
  );
}

export default function MediaPage() {
  return <CmsLayout><Library /></CmsLayout>;
}
